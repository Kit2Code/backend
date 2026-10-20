"""Unpacking uploads safely: hostile zips and folders are refused and leave nothing behind."""

import os
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from grader_common.importer.archive import (
    DEFAULT_LIMITS,
    Limits,
    copy_tree,
    extract_zip,
    unwrap_single_folder,
)
from grader_common.importer.errors import ProblemImportError

SMALL = Limits(max_files=10, max_total_bytes=1000, max_file_bytes=300)


def make_zip(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


def entry_with_mode(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    return info


def assert_refused(zip_path: Path, dest: Path, limits: Limits = DEFAULT_LIMITS) -> None:
    with pytest.raises(ProblemImportError):
        extract_zip(zip_path, dest, limits)
    assert not dest.exists(), "a refused archive must leave nothing behind"


# --- zips that are fine ------------------------------------------------------------------


def test_normal_zip_is_extracted(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "ok.zip",
        [("problem.yaml", b"slug: x\n"), ("tests/01.in", b"1 2\n"), ("tests/", b"")],
    )
    dest = tmp_path / "out"
    extract_zip(archive, dest)
    assert (dest / "problem.yaml").read_bytes() == b"slug: x\n"
    assert (dest / "tests" / "01.in").read_bytes() == b"1 2\n"


def test_macos_junk_is_skipped(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "mac.zip",
        [("task.yaml", b"a"), ("__MACOSX/._task.yaml", b"junk"), ("sub/.DS_Store", b"junk")],
    )
    dest = tmp_path / "out"
    extract_zip(archive, dest)
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")) == ["task.yaml"]


def test_single_wrapping_folder_is_unwrapped(tmp_path: Path) -> None:
    (tmp_path / "aplusb" / "tests").mkdir(parents=True)
    assert unwrap_single_folder(tmp_path) == tmp_path / "aplusb"
    (tmp_path / "other.txt").write_text("x")
    assert unwrap_single_folder(tmp_path) == tmp_path


# --- hostile names and file types --------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../x", "a/../../x", "/etc/x", "C:/x", "a\\..\\x", "a/./b", "a//b", ".", "a/.."],
)
def test_unsafe_names_are_refused(tmp_path: Path, name: str) -> None:
    archive = make_zip(tmp_path / "bad.zip", [("fine.txt", b"ok"), (name, b"evil")])
    assert_refused(archive, tmp_path / "out")
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize(
    "mode",
    [stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o644, stat.S_IFBLK | 0o644, stat.S_IFIFO | 0o644],
    ids=["symlink", "char-device", "block-device", "fifo"],
)
def test_links_and_special_files_are_refused(tmp_path: Path, mode: int) -> None:
    archive = make_zip(tmp_path / "bad.zip", [(entry_with_mode("x", mode), b"/etc/passwd")])
    assert_refused(archive, tmp_path / "out")


def test_duplicate_names_are_refused(tmp_path: Path) -> None:
    with pytest.warns(UserWarning):  # zipfile itself warns about the duplicate
        archive = make_zip(tmp_path / "dup.zip", [("a.txt", b"1"), ("a.txt", b"2")])
    assert_refused(archive, tmp_path / "out")


def test_encrypted_entries_are_refused(tmp_path: Path) -> None:
    # zipfile can't write encrypted entries, so set the "encrypted" flag bit in the headers by hand.
    archive = make_zip(tmp_path / "enc.zip", [("secret.txt", b"data")])
    data = bytearray(archive.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = data.index(signature)
        (flags,) = struct.unpack_from("<H", data, start + flag_offset)
        struct.pack_into("<H", data, start + flag_offset, flags | 0x1)
    archive.write_bytes(bytes(data))
    assert_refused(archive, tmp_path / "out")


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    fake = tmp_path / "fake.zip"
    fake.write_bytes(b"this is not a zip file")
    assert_refused(fake, tmp_path / "out")


# --- size and count limits ---------------------------------------------------------------


def test_20000_files_are_refused(tmp_path: Path) -> None:
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [(f"f{i}", b"") for i in range(20_000)]
    archive = make_zip(tmp_path / "many.zip", entries)
    assert_refused(archive, tmp_path / "out")


@pytest.fixture(scope="session")
def two_gb_bomb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small zip that unpacks to 2.1 GB of zeros: 9 files of 240 MB, each under the file cap."""
    path = tmp_path_factory.mktemp("bomb") / "bomb.zip"
    chunk = bytes(16 << 20)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for i in range(9):
            with zf.open(f"tests/{i}.in", "w", force_zip64=True) as f:
                for _ in range(15):  # 15 x 16 MB = 240 MB
                    f.write(chunk)
    return path


def test_2gb_zip_bomb_is_refused(tmp_path: Path, two_gb_bomb: Path) -> None:
    assert two_gb_bomb.stat().st_size < 50 << 20
    assert_refused(two_gb_bomb, tmp_path / "out")


def test_single_file_over_the_cap_is_refused(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "big.zip", [("big.out", bytes(301))])
    assert_refused(archive, tmp_path / "out", SMALL)


def test_total_over_the_cap_is_refused(tmp_path: Path) -> None:
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [(f"{i}.in", bytes(250)) for i in range(5)]
    archive = make_zip(tmp_path / "total.zip", entries)
    assert_refused(archive, tmp_path / "out", SMALL)


def test_too_many_files_with_small_limit(tmp_path: Path) -> None:
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [(f"{i}", b"") for i in range(11)]
    archive = make_zip(tmp_path / "count.zip", entries)
    assert_refused(archive, tmp_path / "out", SMALL)


def test_a_lying_size_header_is_caught(tmp_path: Path) -> None:
    """The central directory claims 10 bytes, but the data really unpacks to 2000."""
    archive = make_zip(tmp_path / "liar.zip", [("x.in", bytes(2000))])
    data = bytearray(archive.read_bytes())
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<I", data, central + 24, 10)  # uncompressed size field
    archive.write_bytes(bytes(data))
    assert_refused(archive, tmp_path / "out", SMALL)


# --- copying a folder (the CLI also accepts directories) ---------------------------------


def make_tree(root: Path) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "problem.yaml").write_text("slug: x\n")
    (root / "tests" / "01.in").write_text("1 2\n")
    return root


def test_folder_is_copied(tmp_path: Path) -> None:
    src = make_tree(tmp_path / "src")
    dest = tmp_path / "out"
    copy_tree(src, dest)
    assert (dest / "tests" / "01.in").read_text() == "1 2\n"


def test_symlink_inside_folder_is_refused(tmp_path: Path) -> None:
    src = make_tree(tmp_path / "src")
    (src / "tests" / "02.in").symlink_to("/etc/passwd")
    with pytest.raises(ProblemImportError, match="symlink"):
        copy_tree(src, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_symlinked_folder_is_refused(tmp_path: Path) -> None:
    src = make_tree(tmp_path / "src")
    link = tmp_path / "link"
    link.symlink_to(src)
    with pytest.raises(ProblemImportError, match="symlink"):
        copy_tree(link, tmp_path / "out")


def test_fifo_inside_folder_is_refused(tmp_path: Path) -> None:
    src = make_tree(tmp_path / "src")
    os.mkfifo(src / "pipe")
    with pytest.raises(ProblemImportError):
        copy_tree(src, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_folder_limits_apply(tmp_path: Path) -> None:
    src = make_tree(tmp_path / "src")
    (src / "tests" / "big.out").write_bytes(bytes(301))
    with pytest.raises(ProblemImportError):
        copy_tree(src, tmp_path / "out", SMALL)
    assert not (tmp_path / "out").exists()


def test_backslash_in_a_folder_name_is_refused(tmp_path: Path) -> None:
    src = make_tree(tmp_path / "src")
    (src / "a\\b").write_text("x")
    with pytest.raises(ProblemImportError):
        copy_tree(src, tmp_path / "out")
