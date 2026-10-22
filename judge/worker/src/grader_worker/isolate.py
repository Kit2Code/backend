"""Run programs inside isolate boxes.

Every compile, contestant run and checker run goes through `Box.run`, which always passes every
limit (CPU time, wall time, memory, processes, file size, stack) and never `--share-net`. The box
sees isolate's default read-only system folders plus its own /box, with one change: its /dev is
not the worker's /dev (which, in a privileged container, holds the host's kernel log, disks,
FUSE, ...) but BOX_DEV_DIR, a read-only folder with only null, zero, full, random and urandom that
the worker's start-up creates. isolate still mounts a private tmpfs on /dev/shm inside it.
Files go into the box by copying, and come out with O_NOFOLLOW and a size cap, because anything a
contestant leaves in the box is hostile.
"""

import contextlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from grader_worker.verdicts import Meta, parse_meta

FIRST_BOX, LAST_BOX = 100, 109
ISOLATE = "isolate"
# Deadlines for isolate itself (not for the program, which isolate limits): it can hang if its
# helper process dies while starting a box. A run gets its wall-time limit plus this margin;
# --init and --cleanup get ADMIN_TIMEOUT_SECONDS. A missed deadline is an IsolateError.
RUN_GRACE_SECONDS = 30.0
ADMIN_TIMEOUT_SECONDS = 60.0
BOX_ENV = ["HOME=/box", "PATH=/usr/local/bin:/usr/bin:/bin"]
# The box's whole /dev (made by grader_worker.setup; root-owned, outside isolate's box_root).
BOX_DEV_DIR = "/var/local/lib/grader-judge-dev"
BOX_DEVICES = ("null", "zero", "full", "random", "urandom")
_BOX_FILE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")


class IsolateError(RuntimeError):
    """isolate itself failed (not the program inside it). The submission gets JE."""


@dataclass(frozen=True)
class Limits:
    """Limits for one run. Times in seconds, sizes in KB."""

    cpu_time: float
    wall_time: float
    extra_time: float
    memory_kb: int
    processes: int
    fsize_kb: int
    stack_kb: int

    def __post_init__(self) -> None:
        positive = [self.cpu_time, self.wall_time, self.memory_kb, self.processes, self.fsize_kb]
        if min(positive) <= 0 or self.stack_kb <= 0 or self.extra_time < 0:
            raise ValueError(f"limits must be positive: {self}")


@dataclass(frozen=True)
class RunResult:
    meta: Meta
    returncode: int


def check_box_id(box_id: int) -> int:
    if not FIRST_BOX <= box_id <= LAST_BOX:
        raise ValueError(f"box {box_id} is not ours (only {FIRST_BOX}-{LAST_BOX})")
    return box_id


def check_box_file(name: str) -> str:
    """A plain file name directly inside the box (no folders, no '..')."""
    if not _BOX_FILE_RE.fullmatch(name):
        raise ValueError(f"{name!r} is not a plain file name")
    return name


def run_argv(
    box_id: int,
    argv: list[str],
    limits: Limits,
    *,
    meta: str,
    stdin: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> list[str]:
    """The isolate command line for one run. The program comes after `--`, so nothing in it can
    be read as an isolate option."""
    command = [ISOLATE, "--cg", f"--box-id={check_box_id(box_id)}", f"--meta={meta}"]
    command += [
        f"--time={limits.cpu_time:g}",
        f"--wall-time={limits.wall_time:g}",
        f"--extra-time={limits.extra_time:g}",
        f"--cg-mem={limits.memory_kb}",
        f"--processes={limits.processes}",
        f"--fsize={limits.fsize_kb}",
        f"--stack={limits.stack_kb}",
    ]
    # Replaces isolate's default `dev` rule (the worker's whole /dev). Read-only; `dev` lets the
    # device nodes in it work.
    command.append(f"--dir=/dev={BOX_DEV_DIR}:dev")
    for variable in BOX_ENV:
        command += ["--env", variable]
    for flag, name in (("--stdin", stdin), ("--stdout", stdout), ("--stderr", stderr)):
        if name is not None:
            command.append(f"{flag}={check_box_file(name)}")
    return [*command, "--run", "--", *argv]


class Box:
    """One isolate box, used as `with Box(100) as box:`. Cleaned up when the block ends.

    If a cleanup fails the box may still hold a running process or files, so `broken` is set and
    the slot must not be reused (see `Slot.box`). A failed first cleanup stops here with
    IsolateError, before the box is used.
    """

    def __init__(self, box_id: int) -> None:
        self.box_id = check_box_id(box_id)
        self.path: Path | None = None
        self.broken = False

    def __enter__(self) -> Self:
        # A box left over from a crash is cleaned first (isolate succeeds if there is nothing).
        try:
            _isolate(["--cg", f"--box-id={self.box_id}", "--cleanup"], check=True)
        except IsolateError:
            self.broken = True
            raise
        try:
            output = _isolate(["--cg", f"--box-id={self.box_id}", "--init"], check=True)
        except IsolateError:
            # A half-made box: don't reuse the slot as if nothing happened; tidy up if we can.
            self.broken = True
            with contextlib.suppress(IsolateError):
                _isolate(["--cg", f"--box-id={self.box_id}", "--cleanup"], check=True)
            raise
        self.path = Path(output.strip()) / "box"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            _isolate(["--cg", f"--box-id={self.box_id}", "--cleanup"], check=True)
        except IsolateError:
            self.broken = True
            if exc is None:
                raise

    def put(self, name: str, data: bytes, executable: bool = False) -> None:
        """Create a new file in the box (never through a link a previous run left behind)."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(self._file(name), flags, 0o755 if executable else 0o644)
        with os.fdopen(fd, "wb") as out:
            out.write(data)

    def get(self, name: str, max_bytes: int) -> bytes | None:
        """Read a regular file the program wrote, up to `max_bytes`. None if it isn't there."""
        try:
            fd = os.open(
                self._file(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            )
        except (FileNotFoundError, OSError):
            return None
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return None
            return source.read(max_bytes)

    def size(self, name: str) -> int:
        """Size of a regular file in the box, 0 if it isn't one."""
        try:
            info = os.lstat(self._file(name))
        except OSError:
            return 0
        return info.st_size if stat.S_ISREG(info.st_mode) else 0

    def run(
        self,
        argv: list[str],
        limits: Limits,
        *,
        stdin: str | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> RunResult:
        """Run `argv` in the box with `limits`; the meta file lives outside the box."""
        with tempfile.TemporaryDirectory(prefix="meta-") as folder:
            meta_path = os.path.join(folder, "meta")
            command = run_argv(
                self.box_id, argv, limits, meta=meta_path, stdin=stdin, stdout=stdout, stderr=stderr
            )
            # Without --stdin, isolate passes its own fd 0 on to the program: give it /dev/null,
            # never the worker's terminal or pipe.
            deadline = limits.wall_time + RUN_GRACE_SECONDS
            try:
                completed = subprocess.run(  # noqa: S603
                    command, stdin=subprocess.DEVNULL, capture_output=True, check=False,
                    timeout=deadline,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                raise IsolateError(f"isolate did not finish within {deadline:.0f} s") from None
            if completed.returncode > 1:  # 0 = ran fine, 1 = the program failed, else isolate did
                raise IsolateError(_short(completed.stderr))
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = parse_meta(f.read(64 * 1024))
            except (OSError, ValueError) as error:
                raise IsolateError(f"bad meta file: {error}") from None
        return RunResult(meta=meta, returncode=completed.returncode)

    def _file(self, name: str) -> Path:
        if self.path is None:
            raise IsolateError("box is not initialised")
        return self.path / check_box_file(name)


def _isolate(args: list[str], check: bool = False) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            [ISOLATE, *args], stdin=subprocess.DEVNULL, capture_output=True, check=False,
            timeout=ADMIN_TIMEOUT_SECONDS,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise IsolateError(
            f"isolate {' '.join(args)} did not finish within {ADMIN_TIMEOUT_SECONDS:.0f} s"
        ) from None
    if check and completed.returncode != 0:
        raise IsolateError(f"isolate {' '.join(args)}: {_short(completed.stderr)}")
    return completed.stdout.decode("utf-8", "replace")


def _short(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").strip()[:500]
