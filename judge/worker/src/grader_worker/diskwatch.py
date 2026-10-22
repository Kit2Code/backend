"""Watch a running box's disk use, and stop the box when it uses too much.

isolate limits each file (`--fsize`) but not a box's total. While a program runs, a small thread
checks, every `interval` seconds:

- the disk space the box folder really uses (allocated blocks, so a sparse file doesn't count
  more than it uses), against `space_limit`;
- the bytes the box's processes have actually written to disk (the box cgroup's `io.stat`, when
  the io controller is on), against `write_limit`. This catches a program that writes, deletes and
  writes again: it never holds much, but keeps the NAS's disk busy.

Past either limit it calls `on_exceed` once (which kills the box's processes) and remembers why,
so the test becomes OLE. A box the watch can't check is stopped the same way: a folder it can't
read (for example nested deeper than a path can be long), more than MAX_ENTRIES files and folders,
or any error in the watch itself. A file the program deleted but keeps open isn't in any folder;
its data still counts as written, and the box's memory limit and time limit bound it. Files keep
living on disk, so a program's memory is counted like in CMS.
docs/DECISIONS.md, "Disk limits per box".
"""

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

Reason = Literal["space", "written", "files", "unchecked"]

# More files and folders than any real solution makes; walking more would also cost the worker
# real CPU time every interval.
MAX_ENTRIES = 20_000
# Longer paths than any real solution makes; much longer ones (past PATH_MAX, 4096) can't be read by
# path at all, and walking them is slow.
MAX_PATH = 2048


class Unchecked(Exception):
    """A folder in the box can't be read, so its size is unknown."""


class TooManyFiles(Exception):
    """The box has more than MAX_ENTRIES files and folders."""


_WBYTES = re.compile(r"\bwbytes=(\d+)")


def used_bytes(folder: Path, max_entries: int = MAX_ENTRIES) -> int:
    """Disk space used by everything under `folder` (allocated blocks), never following links.

    Files and folders that disappear while it counts are skipped. Raises Unchecked if a folder
    can't be read, and TooManyFiles past `max_entries` entries.
    """
    total = 0
    seen = 0
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except FileNotFoundError:
            continue
        except OSError as error:
            raise Unchecked(str(error)) from None
        seen += len(entries)
        if seen > max_entries:
            raise TooManyFiles
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
                if entry.is_dir(follow_symlinks=False):
                    if len(entry.path) > MAX_PATH:
                        raise Unchecked(f"a folder nested more than {MAX_PATH} characters deep")
                    pending.append(Path(entry.path))
            except OSError:
                continue
            total += info.st_blocks * 512
    return total


def written_bytes(io_stat: str) -> int:
    """Bytes written to disk, over every device, from a cgroup's io.stat."""
    return sum(int(match) for match in _WBYTES.findall(io_stat))


class DiskWatch:
    """`with DiskWatch(...) as watch:` around a run; afterwards `watch.exceeded` says why it was
    stopped ("space" or "written"), or is None."""

    def __init__(
        self,
        folder: Path,
        *,
        space_limit: int,
        write_limit: int | None,
        io_stat: Path | None,
        on_exceed: Callable[[], None],
        interval: float = 0.1,
    ) -> None:
        self.folder = folder
        self.space_limit = space_limit
        self.write_limit = write_limit
        self.io_stat = io_stat
        self.on_exceed = on_exceed
        self.interval = interval
        self.exceeded: Reason | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="disk-watch", daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join()

    def _watch(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                reason = self._over_limit()
            except TooManyFiles:
                reason = "files"
            except Exception:  # noqa: BLE001 - an unwatched box must not keep running
                reason = "unchecked"
            if reason is not None:
                self.exceeded = reason
                self.on_exceed()
                return

    def _over_limit(self) -> Reason | None:
        if used_bytes(self.folder) > self.space_limit:
            return "space"
        if self.write_limit is not None and self.io_stat is not None:
            try:
                text = self.io_stat.read_text()
            except OSError:
                return None  # the box cgroup isn't there (yet, or any more)
            if written_bytes(text) > self.write_limit:
                return "written"
        return None
