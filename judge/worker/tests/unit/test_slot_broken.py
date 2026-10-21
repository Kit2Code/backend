"""A box whose cleanup fails is never used again, and judging reports it (isolate is faked here)."""

import subprocess
from pathlib import Path

import pytest

from grader_worker import config
from grader_worker.isolate import Box, IsolateError, Limits
from grader_worker.slots import SlotPool

LIMITS = Limits(
    cpu_time=1, wall_time=3, extra_time=0, memory_kb=65536, processes=1, fsize_kb=1024,
    stack_kb=8192,
)  # fmt: skip
SETTINGS = config.Settings(slots=2, box_cpus=[10, 11], mem_total=1 << 30)


class FakeIsolate:
    """Stands in for `subprocess.run` of isolate; the cleanups numbered in `failing` fail."""

    def __init__(self, box_parent: Path, failing: set[int]) -> None:
        self.box_parent = box_parent
        self.failing = failing
        self.cleanups = 0
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        if "--cleanup" in command:
            self.cleanups += 1
            if self.cleanups in self.failing:
                return subprocess.CompletedProcess(command, 2, b"", b"Cannot remove box")
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if "--init" in command:
            (self.box_parent / "box").mkdir(exist_ok=True)
            return subprocess.CompletedProcess(command, 0, f"{self.box_parent}\n".encode(), b"")
        raise AssertionError(f"unexpected isolate call {command}")


def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failing: set[int]) -> FakeIsolate:
    isolate = FakeIsolate(tmp_path, failing)
    monkeypatch.setattr(subprocess, "run", isolate)
    return isolate


def test_a_failed_first_cleanup_stops_the_box(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolate = fake(monkeypatch, tmp_path, failing={1})
    with pytest.raises(IsolateError, match="cleanup"):
        with Box(100):
            pass
    assert not any("--init" in command for command in isolate.commands)


def test_a_failed_last_cleanup_marks_the_slot_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake(monkeypatch, tmp_path, failing={2})
    state = tmp_path / "state"
    pool = SlotPool(SETTINGS, state_dir=state)
    with pool.acquire() as slot:
        with pytest.raises(IsolateError):
            with slot.box(LIMITS):
                pass
    assert (state / f"broken-{slot.index}").exists()


def test_a_failed_first_cleanup_marks_the_slot_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake(monkeypatch, tmp_path, failing={1})
    state = tmp_path / "state"
    pool = SlotPool(SETTINGS, state_dir=state)
    with pool.acquire() as slot:
        with pytest.raises(IsolateError):
            with slot.box(LIMITS):
                pass
    assert (state / f"broken-{slot.index}").exists()


def test_the_slot_is_marked_broken_even_when_the_step_itself_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake(monkeypatch, tmp_path, failing={2})
    state = tmp_path / "state"
    pool = SlotPool(SETTINGS, state_dir=state)
    with pool.acquire() as slot:
        with pytest.raises(KeyError):
            with slot.box(LIMITS):
                raise KeyError("the step failed")
    assert (state / f"broken-{slot.index}").exists()


def test_a_broken_slot_is_skipped(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "broken-0").write_text("")
    pool = SlotPool(SETTINGS, state_dir=state)
    for _ in range(3):
        with pool.acquire() as slot:
            assert slot.index == 1
            assert slot.box_id == 101


def test_when_every_slot_is_broken_acquire_fails_clearly(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "broken-0").write_text("")
    (state / "broken-1").write_text("")
    pool = SlotPool(SETTINGS, state_dir=state)
    with pytest.raises(IsolateError, match="broken"):
        with pool.acquire(poll_seconds=0.01):
            pass


def test_a_good_box_leaves_no_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake(monkeypatch, tmp_path, failing=set())
    state = tmp_path / "state"
    pool = SlotPool(SETTINGS, state_dir=state)
    with pool.acquire() as slot, slot.box(LIMITS) as box:
        assert box.path == tmp_path / "box"
    assert not list(state.glob("broken-*"))
