"""The isolate command line: every run gets every limit, and nothing that opens the sandbox."""

import subprocess
from pathlib import Path

import pytest

from grader_worker import isolate
from grader_worker.isolate import Limits, run_argv

LIMITS = Limits(
    cpu_time=1.5, wall_time=4.0, extra_time=0.5, memory_kb=262144, processes=1, fsize_kb=65536,
    stack_kb=262144,
)  # fmt: skip


def test_every_limit_is_passed() -> None:
    argv = run_argv(105, ["./main"], LIMITS, meta="/run/meta.txt", stdin="in.txt", stdout="out.txt")
    assert argv[:4] == ["isolate", "--cg", "--box-id=105", "--meta=/run/meta.txt"]
    for flag in [
        "--time=1.5",
        "--wall-time=4",
        "--extra-time=0.5",
        "--cg-mem=262144",
        "--processes=1",
        "--fsize=65536",
        "--stack=262144",
        "--stdin=in.txt",
        "--stdout=out.txt",
    ]:
        assert flag in argv
    assert argv[-3:] == ["--run", "--", "./main"]


def test_the_program_is_after_the_double_dash() -> None:
    # Arguments that look like isolate options must not be read as options.
    argv = run_argv(100, ["./main", "--share-net", "--dir=/=/"], LIMITS, meta="/m")
    separator = argv.index("--")
    assert "--share-net" not in argv[:separator]
    assert "--dir=/=/" not in argv[:separator]
    dirs = [a for a in argv[:separator] if a.startswith("--dir")]
    assert dirs == [f"--dir=/dev={isolate.BOX_DEV_DIR}:dev"]
    assert argv[separator + 1 :] == ["./main", "--share-net", "--dir=/=/"]


def test_the_environment_is_minimal() -> None:
    argv = run_argv(100, ["./main"], LIMITS, meta="/m")
    env = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert env == ["HOME=/box", "PATH=/usr/local/bin:/usr/bin:/bin"]
    assert "--full-env" not in argv


@pytest.mark.parametrize("box_id", [99, 0, 110, -1])
def test_only_our_boxes_are_used(box_id: int) -> None:
    with pytest.raises(ValueError):
        run_argv(box_id, ["./main"], LIMITS, meta="/m")


@pytest.mark.parametrize("name", ["../x", "/etc/passwd", "a/b", "", ".", "x\ny"])
def test_redirect_names_are_plain_box_files(name: str) -> None:
    with pytest.raises(ValueError):
        run_argv(100, ["./main"], LIMITS, meta="/m", stdout=name)


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Limits(
            cpu_time=0, wall_time=1, extra_time=0, memory_kb=1, processes=1, fsize_kb=1, stack_kb=1
        )


def test_isolate_never_passes_on_the_worker_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --stdin, isolate hands its own fd 0 to the program, so isolate must get /dev/null."""
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(kwargs)
        if "--meta" in " ".join(command):
            meta = next(a for a in command if a.startswith("--meta="))[len("--meta=") :]
            Path(meta).write_text("time:0.001\ntime-wall:0.002\nmax-rss:100\nexitcode:0\n")
        return subprocess.CompletedProcess(command, 0, b"/var/local/lib/isolate/100\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with isolate.Box(100) as box:
        box.run(["/box/main"], LIMITS)
    assert calls
    assert all(kwargs.get("stdin") is subprocess.DEVNULL for kwargs in calls)


def test_the_box_gets_only_the_small_dev_folder() -> None:
    """isolate's default /dev rule shows every device the worker sees; ours replaces it with a
    read-only folder of five harmless devices (made by the worker's start-up)."""
    argv = run_argv(100, ["./main"], LIMITS, meta="/m")
    separator = argv.index("--")
    rules = [a for a in argv[:separator] if a.startswith(("--dir", "-d"))]
    assert rules == [f"--dir=/dev={isolate.BOX_DEV_DIR}:dev"]
    assert not any(":rw" in rule or ":tmp" in rule for rule in rules)
