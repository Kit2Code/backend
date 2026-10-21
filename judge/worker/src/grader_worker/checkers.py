"""Deciding a test's outcome (0 to 1) from the contestant's output.

- diff: CMS white-diff (cms/grading/steps/whitediff.py).
- cms: the CMS "comparator" protocol (cms/grading/steps/trusted.py, checker_step): run
  `checker <input> <correct output> <contestant output>`; the first line of stdout is the outcome,
  a number from 0 to 1, and the first line of stderr is the message (often a `translate:...` key).
- testlib: run `checker <input> <contestant output> <answer>`; testlib.h (0.9.41, resultExitCode)
  exits 0 = ok, 1 = wrong answer, 2 = presentation error, 3 = checker failure, 4 = extra data,
  7 = partial points. Partial points aren't supported here, so 7 is a judge error.

Checkers are compiled in the sandbox with CMS's C++17 flags, cached by a hash of their source,
and run in a fresh box for every test. Like CMS, a missing output file is wrong without asking
the checker. A checker that crashes, times out or prints nonsense makes the test a judge error.
"""

import contextlib
import hashlib
import io
import math
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from grader_common.problem_format import Problem
from grader_worker import config, limits
from grader_worker.languages import LANGUAGES
from grader_worker.slots import Slot
from grader_worker.whitediff import white_diff

MAX_CHECKER_BYTES = 64 << 20
MAX_MESSAGE = 1024
ELF_MAGIC = b"\x7fELF"
TESTLIB_WRONG = {1: "wrong answer", 2: "presentation error", 4: "unexpected extra output"}


@dataclass(frozen=True)
class CheckResult:
    """outcome None means the checker itself failed: the test gets JE."""

    outcome: float | None
    message: str


class CheckerError(Exception):
    """The checker can't be used (it doesn't compile); every test gets JE."""


@dataclass(frozen=True)
class Checker:
    """A ready-to-run checker: `kind` is diff, cms or testlib; `binary` is None for diff."""

    kind: str
    binary: bytes | None = None

    def check(
        self, slot: Slot, test_input: bytes, correct: bytes, output: bytes | None
    ) -> CheckResult:
        if output is None:
            return CheckResult(0.0, "no output")
        if self.kind == "diff" or self.binary is None:
            return CheckResult(
                1.0 if white_diff(io.BytesIO(output), io.BytesIO(correct)) else 0.0, ""
            )
        files = (
            ["input", "correct", "output"] if self.kind == "cms" else ["input", "output", "correct"]
        )
        with slot.box(limits.CHECKER) as box:
            box.put("checker", self.binary, executable=True)
            box.put("input", test_input)
            box.put("correct", correct)
            box.put("output", output)
            result = box.run(
                ["./checker", *files], limits.CHECKER, stdout="_stdout", stderr="_stderr"
            )
            stdout = _first_line(box.get("_stdout", 4096))
            message = _first_line(box.get("_stderr", 4096))
        meta = result.meta
        if self.kind == "cms":
            if meta.status is not None:
                return CheckResult(None, f"checker failed ({meta.status})")
            try:
                outcome = float(stdout)
            except ValueError:
                return CheckResult(None, "checker printed no outcome")
            if not (math.isfinite(outcome) and 0.0 <= outcome <= 1.0):
                return CheckResult(None, f"checker outcome {stdout!r} is not between 0 and 1")
            return CheckResult(outcome, message)
        # testlib
        if meta.status is None:
            return CheckResult(1.0, message)
        if meta.status == "RE" and meta.exitcode in TESTLIB_WRONG:
            return CheckResult(0.0, message or TESTLIB_WRONG[meta.exitcode])
        if meta.status == "RE" and meta.exitcode == 7:
            return CheckResult(None, "testlib partial points are not supported")
        return CheckResult(None, f"checker failed: {message}" if message else "checker failed")


def prepare(
    problem: Problem, problem_dir: Path, slot: Slot, cache_dir: Path = config.CACHE_DIR
) -> Checker:
    """Get the problem's checker ready: compiled (or taken from the cache) for cms and testlib."""
    kind = problem.checker.type
    if kind == "diff" or problem.checker.file is None:
        return Checker("diff")
    files = {"checker.cpp": (problem_dir / problem.checker.file).read_bytes()}
    if kind == "testlib":
        files["testlib.h"] = config.TESTLIB_HEADER.read_bytes()
    command = LANGUAGES["cpp17"].compile_commands(["checker.cpp"], "checker")[0]
    key = hashlib.sha256(repr((kind, command, sorted(files.items()))).encode()).hexdigest()
    cached = cache_dir / "checkers" / problem.slug / key / "checker"
    binary = _read_cached(cached)
    if binary is None:
        binary = _compile(slot, files, command)
        _write_cached(cached, binary)
    return Checker(kind, binary)


def _compile(slot: Slot, files: dict[str, bytes], command: list[str]) -> bytes:
    with slot.box(limits.COMPILE) as box:
        for name, data in files.items():
            box.put(name, data)
        result = box.run(command, limits.COMPILE, stdout="_stdout", stderr="_stderr")
        log = _first_line(box.get("_stderr", 4096))
        binary = box.get("checker", MAX_CHECKER_BYTES + 1)
    if result.meta.status is not None or binary is None or len(binary) > MAX_CHECKER_BYTES:
        raise CheckerError(f"the checker doesn't compile: {log}")
    return binary


def _read_cached(path: Path) -> bytes | None:
    """The cached checker, or None when there is none or it is damaged (then it is rebuilt).

    A cache entry is the binary plus a `.sha256` file with its checksum. An entry that is empty,
    isn't an ELF executable or doesn't match its checksum (a power cut in the middle of a write,
    a disk error) is treated as missing, so it can't turn every test into a judge error forever.
    """
    binary = _read_regular(path, MAX_CHECKER_BYTES + 1)
    checksum = _read_regular(_checksum_path(path), 64 + 1)
    if binary is None or checksum is None or len(binary) > MAX_CHECKER_BYTES:
        return None
    if not binary.startswith(ELF_MAGIC):
        return None
    return binary if hashlib.sha256(binary).hexdigest().encode() == checksum else None


def _read_regular(path: Path, max_bytes: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        return f.read(max_bytes)


def _write_cached(path: Path, binary: bytes) -> None:
    """Write the cache entry: the binary first, then its checksum, each one atomically."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_atomically(path, binary)
    _write_atomically(_checksum_path(path), hashlib.sha256(binary).hexdigest().encode())


def _write_atomically(path: Path, data: bytes) -> None:
    """Replace `path` with `data` so that nobody ever sees half a file, even after a power cut.

    The data goes to a temporary file with a unique name in the same folder (so threads and
    processes writing the same entry never share one), is flushed to the disk, and then renamed
    over `path`; the folder is flushed too so the rename itself survives a crash.
    """
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def _checksum_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _first_line(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", "replace").splitlines()[0][:MAX_MESSAGE] if data.strip() else ""
