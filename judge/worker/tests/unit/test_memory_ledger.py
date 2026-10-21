"""The memory ledger: each step reserves its memory limit first and waits if it doesn't fit."""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from grader_worker.slots import MemoryLedger

MB = 1 << 20


def test_reservations_add_up_and_are_released(tmp_path: Path) -> None:
    ledger = MemoryLedger(tmp_path / "memory.json", total=1024 * MB)
    with ledger.reserve(512 * MB):
        assert ledger.used() == 512 * MB
        with ledger.reserve(512 * MB):
            assert ledger.used() == 1024 * MB
    assert ledger.used() == 0


def test_a_reservation_waits_until_memory_is_free(tmp_path: Path) -> None:
    ledger = MemoryLedger(tmp_path / "memory.json", total=768 * MB, poll_seconds=0.01)
    order: list[str] = []
    first_holds = threading.Event()

    def first() -> None:
        with ledger.reserve(512 * MB):
            order.append("first in")
            first_holds.set()
            time.sleep(0.3)
            order.append("first out")

    def second() -> None:
        first_holds.wait()
        with ledger.reserve(512 * MB):
            order.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert order == ["first in", "first out", "second in"]


def test_a_reservation_larger_than_the_total_can_never_fit(tmp_path: Path) -> None:
    ledger = MemoryLedger(tmp_path / "memory.json", total=256 * MB)
    with pytest.raises(ValueError, match="larger than"):
        with ledger.reserve(512 * MB):
            pass


def test_reservations_of_dead_processes_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    pid = os.fork()
    if pid == 0:  # child: reserve and die without releasing
        child = MemoryLedger(path, total=1024 * MB)
        child.reserve(1024 * MB).__enter__()
        os._exit(0)
    os.waitpid(pid, 0)
    ledger = MemoryLedger(path, total=1024 * MB)
    with ledger.reserve(1024 * MB):  # would wait forever if the dead entry still counted
        pass


def test_a_corrupt_ledger_file_is_reset(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text("{not json")
    ledger = MemoryLedger(path, total=1024 * MB)
    with ledger.reserve(100 * MB):
        assert ledger.used() == 100 * MB


def _start_time(pid: int) -> int:
    """Field 22 of /proc/<pid>/stat: when the process started, in clock ticks since boot."""
    stat = Path(f"/proc/{pid}/stat").read_text()
    return int(stat.rsplit(")", 1)[1].split()[19])


def test_each_reservation_records_its_process_start_time(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    ledger = MemoryLedger(path, total=1024 * MB)
    with ledger.reserve(100 * MB):
        [entry] = json.loads(path.read_text())
        assert entry["pid"] == os.getpid()
        assert entry["start"] == _start_time(os.getpid())


def test_an_entry_of_a_reused_pid_is_dropped(tmp_path: Path) -> None:
    # After a container restart a new process can get the same PID as a dead one (PIDs in a
    # container are predictable). Its different start time shows the entry is stale.
    path = tmp_path / "memory.json"
    stale = {"pid": os.getpid(), "start": _start_time(os.getpid()) - 1, "token": "old"}
    path.write_text(json.dumps([{**stale, "bytes": 1024 * MB}]))
    ledger = MemoryLedger(path, total=1024 * MB)
    assert ledger.used() == 0


def test_an_entry_without_a_start_time_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(json.dumps([{"pid": 1, "token": "old", "bytes": 1024 * MB}]))
    ledger = MemoryLedger(path, total=1024 * MB)
    assert ledger.used() == 0


def test_a_live_entry_of_another_process_still_counts(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    parent = os.getppid()
    live = {"pid": parent, "start": _start_time(parent), "token": "t", "bytes": 300 * MB}
    path.write_text(json.dumps([live]))
    ledger = MemoryLedger(path, total=1024 * MB)
    assert ledger.used() == 300 * MB


def test_setup_clears_the_ledger_and_broken_slot_markers(tmp_path: Path) -> None:
    from grader_worker.setup import reset_state

    (tmp_path / "memory.json").write_text(json.dumps([{"pid": 7, "token": "x", "bytes": 1}]))
    (tmp_path / "broken-0").write_text("")
    (tmp_path / "broken-1").write_text("")
    (tmp_path / "slot-0.lock").write_text("")
    reset_state(tmp_path)
    assert MemoryLedger(tmp_path / "memory.json", total=1024 * MB).used() == 0
    assert not list(tmp_path.glob("broken-*"))
    assert (tmp_path / "slot-0.lock").exists()  # lock files are left alone
