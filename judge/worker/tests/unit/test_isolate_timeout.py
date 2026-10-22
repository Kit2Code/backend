"""isolate itself can hang (seen once: its helper process died while starting a box), so every
call to it has a deadline; a hung call becomes an IsolateError instead of a stuck judging slot."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from grader_worker import isolate
from grader_worker.isolate import IsolateError, Limits

LIMITS = Limits(
    cpu_time=1.5, wall_time=4.0, extra_time=0.5, memory_kb=262144, processes=1, fsize_kb=65536,
    stack_kb=262144,
)  # fmt: skip


def fake_isolate(hang: str = "") -> tuple[list[dict[str, Any]], Callable[..., Any]]:
    """A stand-in for subprocess.run: records each call; the call containing `hang` times out."""
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"command": command, **kwargs})
        if hang and hang in command:
            raise subprocess.TimeoutExpired(command, float(kwargs["timeout"]))
        if "--run" in command:
            meta = next(a for a in command if a.startswith("--meta="))[len("--meta=") :]
            Path(meta).write_text("time:0.001\ntime-wall:0.002\nmax-rss:100\nexitcode:0\n")
        return subprocess.CompletedProcess(command, 0, b"/var/local/lib/isolate/100\n", b"")

    return calls, run


def test_every_call_to_isolate_has_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, run = fake_isolate()
    monkeypatch.setattr(subprocess, "run", run)
    with isolate.Box(100) as box:
        box.run(["/box/main"], LIMITS)
    actions = ("--init", "--run", "--cleanup")
    by_action = {next(a for a in c["command"] if a in actions): c for c in calls}
    assert by_action["--run"]["timeout"] == LIMITS.wall_time + isolate.RUN_GRACE_SECONDS
    assert by_action["--init"]["timeout"] == isolate.ADMIN_TIMEOUT_SECONDS
    assert by_action["--cleanup"]["timeout"] == isolate.ADMIN_TIMEOUT_SECONDS


def test_a_run_that_never_ends_is_an_isolate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, run = fake_isolate(hang="--run")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(IsolateError, match="did not finish"), isolate.Box(100) as box:
        box.run(["/box/main"], LIMITS)
    assert any("--cleanup" in c["command"] for c in calls)


def test_an_init_that_never_ends_is_an_isolate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _, run = fake_isolate(hang="--init")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(IsolateError, match="did not finish"), isolate.Box(100):
        pass


def test_a_box_whose_init_fails_is_marked_broken_and_cleaned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, run = fake_isolate(hang="--init")
    monkeypatch.setattr(subprocess, "run", run)
    box = isolate.Box(100)
    with pytest.raises(IsolateError):
        box.__enter__()
    assert box.broken  # the slot is not reused as if nothing happened
    actions = [next(a for a in c["command"] if a in ("--init", "--cleanup")) for c in calls]
    assert actions == ["--cleanup", "--init", "--cleanup"]  # one best-effort cleanup after it
