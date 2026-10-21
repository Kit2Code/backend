"""Unexpected failures while judging become JE results, and messages from boxes are cleaned.

These use a fake slot instead of isolate, so they only check judge()'s own error handling.
"""

import errno
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from grader_worker import checkers
from grader_worker import judge as judge_module
from grader_worker.isolate import Limits, RunResult
from grader_worker.judge import MAX_MESSAGE_CHARS, judge
from grader_worker.verdicts import Meta

PROBLEM = Path(__file__).resolve().parents[4] / "fixtures" / "hostile" / "problem"
SOURCE = b"int main(void) { return 0; }\n"
OK_META = Meta(
    status=None, message="", time=0.01, wall_time=0.02, max_rss_kb=1024, cg_mem_kb=1024,
    cg_oom_killed=False, killed=False, exitcode=0, exitsig=None,
)  # fmt: skip


@dataclass
class FakeBox:
    """Stores files in a dict; `on_put` may raise to simulate a failure."""

    on_put: Callable[[str], None]
    files: dict[str, bytes] = field(default_factory=dict)

    def put(self, name: str, data: bytes, executable: bool = False) -> None:
        self.on_put(name)
        self.files[name] = data

    def get(self, name: str, max_bytes: int) -> bytes | None:
        if name == "main":  # the compiler "produced" the executable
            return b"\x7fELF"
        return self.files.get(name, b"")[:max_bytes]

    def size(self, name: str) -> int:
        return len(self.files.get(name, b""))

    def run(self, argv: list[str], limits: Limits, **files: str | None) -> RunResult:
        return RunResult(meta=OK_META, returncode=0)


@dataclass
class FakeSlot:
    on_put: Callable[[str], None] = lambda name: None
    on_reserve: Callable[[Limits], None] = lambda limits: None
    box_id: int = 100

    @contextmanager
    def box(self, limits: Limits) -> Iterator[FakeBox]:
        self.on_reserve(limits)
        yield FakeBox(self.on_put)


def _raise(error: Exception) -> Callable[..., None]:
    def fail(*args: object) -> None:
        raise error

    return fail


@pytest.mark.parametrize(
    "error",
    [
        ValueError("'-bad' is not a valid box file name"),
        FileExistsError(errno.EEXIST, "File exists"),
        OSError(errno.ENOSPC, "No space left on device"),
    ],
)
def test_unexpected_error_while_compiling_is_a_judge_error(error: Exception) -> None:
    result = judge(PROBLEM, "c11", SOURCE, FakeSlot(on_put=_raise(error)))  # type: ignore[arg-type]
    assert result.verdict == "JE"
    assert result.score == 0
    assert type(error).__name__ in result.message


def test_memory_reservation_failure_is_a_judge_error() -> None:
    slot = FakeSlot(on_reserve=_raise(ValueError("512 MB is more than the 256 MB total")))
    result = judge(PROBLEM, "c11", SOURCE, slot)  # type: ignore[arg-type]
    assert result.verdict == "JE"
    assert "more than the 256 MB total" in result.message


def test_unexpected_error_in_a_test_is_a_judge_error_for_that_test() -> None:
    def on_put(name: str) -> None:
        if name == "_stdin":
            raise FileExistsError(errno.EEXIST, "File exists", name)

    result = judge(PROBLEM, "c11", SOURCE, FakeSlot(on_put=on_put))  # type: ignore[arg-type]
    assert result.verdict == "JE"
    assert [t.verdict for t in result.tests] == ["JE"]
    assert "FileExistsError" in result.tests[0].message


def test_unexpected_error_preparing_the_checker_is_a_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checkers, "prepare", _raise(OSError(errno.ENOSPC, "full")))
    result = judge(PROBLEM, "c11", SOURCE, FakeSlot())  # type: ignore[arg-type]
    assert result.verdict == "JE"
    assert [t.verdict for t in result.tests] == ["JE"]


def test_error_messages_are_cleaned_and_capped() -> None:
    error = ValueError("\x1b[31m‮evil⁦" + "x" * 100_000)
    result = judge(PROBLEM, "c11", SOURCE, FakeSlot(on_put=_raise(error)))  # type: ignore[arg-type]
    assert result.verdict == "JE"
    assert len(result.message) <= MAX_MESSAGE_CHARS
    assert "\x1b" not in result.message and "‮" not in result.message
    assert "[31mevil" in result.message


def test_invalid_requests_are_still_refused() -> None:
    with pytest.raises(judge_module.SubmissionError):
        judge(PROBLEM, "cpp17", SOURCE, FakeSlot())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "char",
    ["\x00", "\x1b", "\x7f", "\x85", "\x9b", "​", "‎", "‮", "⁦", "⁩", "﻿"],
)
def test_clean_drops_control_and_format_characters(char: str) -> None:
    assert judge_module._clean(f"a{char}b") == "ab"


def test_clean_keeps_text_tabs_and_newlines() -> None:
    text = "line 1:\tผิด\nline 2: wrong answer é ✓"
    assert judge_module._clean(text) == text


def test_a_compile_log_is_capped_in_bytes_not_characters() -> None:
    """Invalid UTF-8 decodes to 3-byte replacement characters; the cap is on the encoded size."""
    log = judge_module._cap_log([("�" * judge_module.MAX_LOG_BYTES)])
    assert len(log.encode("utf-8")) <= judge_module.MAX_LOG_BYTES
    assert log == "�" * (judge_module.MAX_LOG_BYTES // 3)
