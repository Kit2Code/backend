"""Judge slots and the shared memory budget.

Slot i uses isolate box 100+i, pinned to the i-th judge CPU. A slot is held with an exclusive
`flock` on /run/grader-judge/slot-<i>.lock, so two processes in the container (the worker loop and
a `judge-run` started by hand) never use the same box at once; the kernel drops the lock if the
holder dies. If isolate fails to clean a slot's box, the slot is marked broken (a broken-<i> file
in the same folder) and never handed out again until the worker restarts; its step reports JE.

All boxes together may use at most JUDGE_MEM_TOTAL (the memory.max of our cgroup). Before every
step a slot **reserves** that step's memory limit in a small ledger file and waits while it doesn't
fit, so two big jobs can't push each other into the OOM killer and get a false MLE (SPEC "Slots,
CPUs, memory reservation"). The ledger is shared between processes through a lock file; entries
of processes that died are dropped. Each entry keeps its process's PID *and* start time, because
PIDs in a container are predictable: after a restart a new process may get the PID of one that
held memory before, and only the start time tells them apart.
"""

import fcntl
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grader_worker import config
from grader_worker.isolate import Box, IsolateError, Limits

LEDGER_NAME = "memory.json"
BROKEN_PREFIX = "broken-"


class MemoryLedger:
    """Memory reserved by running steps, kept in a JSON file guarded by a lock file."""

    def __init__(self, path: Path, total: int, poll_seconds: float = 0.05) -> None:
        self.path = path
        self.total = total
        self.poll_seconds = poll_seconds

    @contextmanager
    def reserve(self, amount: int) -> Iterator[None]:
        """Hold `amount` bytes of the budget for the `with` block, waiting until it fits."""
        if amount > self.total:
            raise ValueError(
                f"{amount >> 20} MiB is larger than the whole budget ({self.total >> 20} MiB)"
            )
        token = uuid.uuid4().hex
        while not self._try_add(token, amount):
            time.sleep(self.poll_seconds)
        try:
            yield
        finally:
            self._remove(token)

    def used(self) -> int:
        with self._locked() as entries:
            return sum(entry["bytes"] for entry in entries)

    def _try_add(self, token: str, amount: int) -> bool:
        with self._locked() as entries:
            if sum(entry["bytes"] for entry in entries) + amount > self.total:
                return False
            pid = os.getpid()
            start = _start_time(pid)
            if start is None:  # without it the entry would look stale and be dropped
                raise RuntimeError(f"cannot read the start time of process {pid}")
            entries.append({"pid": pid, "start": start, "token": token, "bytes": amount})
            return True

    def _remove(self, token: str) -> None:
        with self._locked() as entries:
            entries[:] = [entry for entry in entries if entry["token"] != token]

    @contextmanager
    def _locked(self) -> Iterator[list[dict[str, Any]]]:
        """Lock the ledger, yield its live entries for reading/changing, then save them."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_fd = os.open(
            self.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            entries = [entry for entry in self._load() if _alive(entry["pid"], entry["start"])]
            yield entries
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(entries))
            os.replace(temporary, self.path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [
            entry
            for entry in data
            if isinstance(entry, dict)
            and isinstance(entry.get("pid"), int)
            and isinstance(entry.get("start"), int)
            and isinstance(entry.get("token"), str)
            and isinstance(entry.get("bytes"), int)
        ]


@dataclass(frozen=True)
class Slot:
    index: int
    box_id: int
    cpu: int
    ledger: MemoryLedger
    state_dir: Path = config.STATE_DIR

    @contextmanager
    def box(self, limits: Limits) -> Iterator[Box]:
        """A fresh box for one step, with the step's memory limit reserved first.

        If isolate can't clean the box (before or after the step), the slot is marked broken and
        IsolateError is raised, so the step reports JE and the box is never used again.
        """
        with self.ledger.reserve(limits.memory_kb * 1024):
            box = Box(self.box_id)
            try:
                with box:
                    yield box
            finally:
                if box.broken:
                    self.mark_broken()

    def mark_broken(self) -> None:
        """Keep this slot out of use until the worker restarts (start-up clears the marks)."""
        (self.state_dir / f"{BROKEN_PREFIX}{self.index}").touch()


class SlotPool:
    def __init__(self, settings: config.Settings, state_dir: Path = config.STATE_DIR) -> None:
        self.settings = settings
        self.state_dir = state_dir
        self.ledger = MemoryLedger(state_dir / LEDGER_NAME, settings.mem_total)
        self._threads = threading.Lock()  # flock doesn't exclude threads of one process

    @contextmanager
    def acquire(self, poll_seconds: float = 0.1) -> Iterator[Slot]:
        """Wait for a free slot and hold it for the duration of the `with` block.

        Broken slots are skipped; if every slot is broken this raises IsolateError instead of
        waiting forever.
        """
        self.state_dir.mkdir(mode=0o700, exist_ok=True)
        while True:
            if all(self._is_broken(index) for index in range(self.settings.slots)):
                raise IsolateError(
                    "every judge slot is broken (isolate could not clean its box); "
                    "restart the worker"
                )
            fd, index = self._try_lock_one()
            if fd is not None:
                try:
                    yield Slot(
                        index=index,
                        box_id=self.settings.box_ids[index],
                        cpu=self.settings.box_cpus[index],
                        ledger=self.ledger,
                        state_dir=self.state_dir,
                    )
                    return
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
            time.sleep(poll_seconds)

    def _try_lock_one(self) -> tuple[int | None, int]:
        with self._threads:
            for index in range(self.settings.slots):
                fd = os.open(
                    self.state_dir / f"slot-{index}.lock",
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
                    0o600,
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    continue
                # Checked while holding the lock: the last holder marks it before letting go.
                if self._is_broken(index):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    continue
                return fd, index
        return None, -1

    def _is_broken(self, index: int) -> bool:
        return (self.state_dir / f"{BROKEN_PREFIX}{index}").exists()


def _start_time(pid: int) -> int | None:
    """When process `pid` started (field 22 of /proc/<pid>/stat, clock ticks since boot), or None
    if there is no such process."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            stat = f.read()
    except OSError:
        return None
    # The command name (field 2) is in parentheses and may contain spaces or ')'.
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        return int(fields[19])  # fields[0] is field 3
    except (IndexError, ValueError):
        return None


def _alive(pid: int, start: int) -> bool:
    """The process that made a ledger entry still runs: same PID *and* same start time."""
    current = _start_time(pid)
    return current is not None and current == start
