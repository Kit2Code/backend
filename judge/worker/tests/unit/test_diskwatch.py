"""The disk watch: stop a box that keeps too much on disk, or writes too much to it."""

import os
import threading
import time
from pathlib import Path

import pytest

from grader_worker.diskwatch import MAX_ENTRIES, DiskWatch, used_bytes, written_bytes

MB = 1 << 20


def test_used_bytes_counts_real_disk_space(tmp_path: Path) -> None:
    (tmp_path / "data").write_bytes(b"x" * MB)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "more").write_bytes(b"x" * MB)
    with open(tmp_path / "sparse", "wb") as file:
        file.truncate(100 * MB)  # a 100 MB hole uses (almost) no disk
    assert 2 * MB <= used_bytes(tmp_path) < 3 * MB


def test_used_bytes_never_follows_links(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"x" * MB)
    box = tmp_path / "box"
    box.mkdir()
    (box / "link").symlink_to(outside)
    assert used_bytes(box) < MB


def test_written_bytes_adds_every_device() -> None:
    io_stat = "8:0 rbytes=10 wbytes=2048 rios=1 wios=2\n8:16 rbytes=0 wbytes=1024 rios=0 wios=1\n"
    assert written_bytes(io_stat) == 3072
    assert written_bytes("") == 0


def grow(folder: Path, total: int, step: int = MB) -> None:
    for index in range(total // step):
        (folder / f"f{index}").write_bytes(b"x" * step)
        time.sleep(0.01)


def test_too_much_on_disk_stops_the_box(tmp_path: Path) -> None:
    calls: list[str] = []
    watch = DiskWatch(tmp_path, space_limit=2 * MB, write_limit=None, io_stat=None,
                      on_exceed=lambda: calls.append("kill"), interval=0.02)  # fmt: skip
    with watch:
        writer = threading.Thread(target=grow, args=(tmp_path, 6 * MB))
        writer.start()
        writer.join()
        time.sleep(0.1)
    assert watch.exceeded == "space"
    assert calls == ["kill"]  # once


def test_too_much_written_stops_the_box(tmp_path: Path) -> None:
    io_stat = tmp_path / "io.stat"
    io_stat.write_text("8:0 rbytes=0 wbytes=0\n")
    box = tmp_path / "box"
    box.mkdir()
    calls: list[str] = []
    with DiskWatch(box, space_limit=100 * MB, write_limit=5 * MB, io_stat=io_stat,
                   on_exceed=lambda: calls.append("kill"), interval=0.02) as watch:  # fmt: skip
        io_stat.write_text(f"8:0 rbytes=0 wbytes={6 * MB}\n")
        time.sleep(0.2)
    assert watch.exceeded == "written"
    assert calls == ["kill"]


def test_a_box_within_its_limits_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "small").write_bytes(b"x" * MB)
    calls: list[str] = []
    with DiskWatch(tmp_path, space_limit=10 * MB, write_limit=None, io_stat=None,
                   on_exceed=lambda: calls.append("kill"), interval=0.02) as watch:  # fmt: skip
        time.sleep(0.1)
    assert watch.exceeded is None and calls == []


def test_the_watch_ignores_files_that_vanish(tmp_path: Path) -> None:
    for index in range(200):
        (tmp_path / f"f{index}").write_bytes(b"x")
    with DiskWatch(tmp_path, space_limit=10 * MB, write_limit=None, io_stat=None,
                   on_exceed=lambda: None, interval=0.001) as watch:  # fmt: skip
        for index in range(200):
            os.unlink(tmp_path / f"f{index}")
    assert watch.exceeded is None


@pytest.mark.parametrize(
    ("env", "space", "written"),
    [({}, 256 * MB, 512 * MB), ({"JUDGE_BOX_DISK_MB": "300", "JUDGE_BOX_WRITE_MB": "2048"},
                                 300 * MB, 2048 * MB)],
)  # fmt: skip
def test_the_limits_are_settings(env: dict[str, str], space: int, written: int) -> None:
    from grader_worker import config

    settings = config.Settings.from_env({"JUDGE_BOX_CPUS": "4,5", **env})
    assert (settings.box_disk_bytes, settings.box_write_bytes) == (space, written)


@pytest.mark.parametrize("value", ["0", "64", "abc"])
def test_a_disk_limit_below_the_output_limit_is_refused(value: str) -> None:
    from grader_worker import config

    with pytest.raises(config.ConfigError, match="JUDGE_BOX_DISK_MB"):
        config.Settings.from_env({"JUDGE_BOX_CPUS": "4,5", "JUDGE_BOX_DISK_MB": value})


def test_a_folder_that_cannot_be_read_stops_the_box(tmp_path: Path) -> None:
    # 2200 nested folders: the full path is longer than PATH_MAX, so it can't be read by path.
    folders = [os.open(tmp_path, os.O_RDONLY)]
    for _ in range(2200):
        os.mkdir("d", dir_fd=folders[-1])
        folders.append(os.open("d", os.O_RDONLY, dir_fd=folders[-1]))
    try:
        calls: list[str] = []
        with DiskWatch(tmp_path, space_limit=100 * MB, write_limit=None, io_stat=None,
                       on_exceed=lambda: calls.append("kill"), interval=0.02) as watch:  # fmt: skip
            time.sleep(0.3)
        assert watch.exceeded == "unchecked" and calls == ["kill"]
    finally:
        os.close(folders.pop())
        while folders:
            parent = folders.pop()
            os.rmdir("d", dir_fd=parent)
            os.close(parent)


def test_too_many_files_stop_the_box(tmp_path: Path) -> None:
    for index in range(MAX_ENTRIES + 10):
        (tmp_path / f"e{index}").touch()
    with DiskWatch(tmp_path, space_limit=100 * MB, write_limit=None, io_stat=None,
                   on_exceed=lambda: None, interval=0.02) as watch:  # fmt: skip
        time.sleep(0.5)
    assert watch.exceeded == "files"


def test_an_error_in_the_watch_stops_the_box(tmp_path: Path) -> None:
    def broken() -> None:
        raise RuntimeError("boom")

    calls: list[str] = []
    watch = DiskWatch(tmp_path, space_limit=100 * MB, write_limit=None, io_stat=None,
                      on_exceed=lambda: calls.append("kill"), interval=0.02)  # fmt: skip
    watch._over_limit = broken  # type: ignore[method-assign]
    with watch:
        time.sleep(0.1)
    assert watch.exceeded == "unchecked" and calls == ["kill"]
