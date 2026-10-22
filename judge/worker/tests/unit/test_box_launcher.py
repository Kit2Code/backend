"""Every program in a box starts through the launcher, which installs our seccomp filter first."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from grader_worker import isolate
from grader_worker.isolate import Limits

LIMITS = Limits(
    cpu_time=1.5, wall_time=4.0, extra_time=0.5, memory_kb=262144, processes=1, fsize_kb=65536,
    stack_kb=262144,
)  # fmt: skip


def test_the_program_runs_through_the_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if "--run" in command:
            runs.append(command)
            meta = next(a for a in command if a.startswith("--meta="))[len("--meta=") :]
            Path(meta).write_text("time:0.001\ntime-wall:0.002\nmax-rss:100\nexitcode:0\n")
        return subprocess.CompletedProcess(command, 0, b"/var/local/lib/isolate/100\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with isolate.Box(100) as box:
        box.run(["/usr/bin/python3", "main.py"], LIMITS)
    (command,) = runs
    program = command[command.index("--") + 1 :]
    assert program == [isolate.BOX_LAUNCHER, "/usr/bin/python3", "main.py"]
