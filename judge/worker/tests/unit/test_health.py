"""The worker's healthcheck (`judge-health`): the judge loop is alive and some slot still works."""

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from grader_worker.cli import judge_health
from grader_worker.client import JudgeLoop
from grader_worker.config import Settings
from grader_worker.health import ALIVE_NAME, MAX_AGE_SECONDS, problems
from grader_worker.slots import BROKEN_PREFIX, SlotPool

SETTINGS = Settings(slots=2, box_cpus=[4, 5], mem_total=1 << 30)


class IdleApi:
    """web-admin with nothing to judge."""

    def hello(self, *args: Any) -> None:
        pass

    def claim(self, *args: Any, **kwargs: Any) -> None:
        return None

    def heartbeat(self, *args: Any) -> list[int]:
        return []


def test_the_loop_keeps_the_alive_file_fresh(tmp_path: Path) -> None:
    loop = JudgeLoop(
        api=IdleApi(),  # type: ignore[arg-type]
        pool=SlotPool(SETTINGS, state_dir=tmp_path),
        settings=SETTINGS, worker_id="w", poll_seconds=0.02, heartbeat_seconds=0.02,
    )  # fmt: skip
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / ALIVE_NAME).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        first = (tmp_path / ALIVE_NAME).stat().st_mtime_ns
        time.sleep(0.1)
        assert (tmp_path / ALIVE_NAME).stat().st_mtime_ns > first
    finally:
        loop.stop()
        thread.join(timeout=5)


def touch(path: Path, age: float) -> None:
    path.touch()
    then = time.time() - age
    os.utime(path, (then, then))


def test_a_fresh_loop_with_working_slots_is_healthy(tmp_path: Path) -> None:
    touch(tmp_path / ALIVE_NAME, 1)
    (tmp_path / f"{BROKEN_PREFIX}0").touch()  # one broken slot of two is still working
    assert problems(SETTINGS, tmp_path) == []


def test_no_alive_file_is_unhealthy(tmp_path: Path) -> None:
    assert problems(SETTINGS, tmp_path) == ["the judge loop has not started"]


def test_a_stale_alive_file_is_unhealthy(tmp_path: Path) -> None:
    touch(tmp_path / ALIVE_NAME, MAX_AGE_SECONDS + 5)
    assert problems(SETTINGS, tmp_path)[0].startswith("the judge loop has been stuck for")


def test_every_slot_broken_is_unhealthy(tmp_path: Path) -> None:
    touch(tmp_path / ALIVE_NAME, 1)
    for index in range(2):
        (tmp_path / f"{BROKEN_PREFIX}{index}").touch()
    assert problems(SETTINGS, tmp_path) == ["every judge slot is broken; restart the worker"]


def test_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                     capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    monkeypatch.setattr("grader_worker.config.STATE_DIR", tmp_path)
    monkeypatch.setenv("JUDGE_BOX_CPUS", "4,5")
    assert judge_health([]) == 1
    assert "has not started" in capsys.readouterr().err
    touch(tmp_path / ALIVE_NAME, 1)
    assert judge_health([]) == 0
