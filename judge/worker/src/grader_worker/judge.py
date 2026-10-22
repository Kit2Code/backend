"""Judge one submission: compile it, run every test, check the outputs, score.

Each step uses a fresh isolate box: compile in one, then each test in its own, so nothing a
program leaves behind can affect the next step. Like CMS, every test is run (no stopping at the
first failure), in name order.
"""

import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grader_common.api import JudgeResult, SubtaskScore, TestResult
from grader_common.importer.native import check_version
from grader_common.problem_format import STREAM_FILES, Problem
from grader_common.scoring import score
from grader_worker import checkers, limits
from grader_worker.isolate import IsolateError, Limits
from grader_worker.languages import LANGUAGES, Language
from grader_worker.slots import Slot
from grader_worker.verdicts import Verdict, outcome_verdict, run_verdict

MAX_SOURCE_BYTES = 64 * 1024
MAX_LOG_BYTES = 64 * 1024
MAX_EXECUTABLE_BYTES = 64 << 20
MAX_MESSAGE_CHARS = 4096

_STDIN, _STDOUT, _STDERR = STREAM_FILES
# Unicode categories dropped from text that came out of a box: control characters (C0, DEL, C1)
# and invisible format characters (bidi overrides and isolates, zero-width spaces, BOM, ...).
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf"})


class SubmissionError(ValueError):
    """The request itself is invalid (unknown language, source too large)."""


@dataclass(frozen=True)
class Compiled:
    executable: bytes | None  # None: compilation failed
    executable_name: str
    log: str


@dataclass(frozen=True)
class Program:
    """Everything about running the compiled submission that is the same for every test."""

    language: Language
    executable_name: str
    executable: bytes
    limits: Limits
    checker: checkers.Checker


def judge(
    problem_dir: Path,
    lang: str,
    source: bytes,
    slot: Slot,
    time_scale: float = 1.0,
    timeline: dict[str, float] | None = None,
    on_test: Callable[[TestResult], None] | None = None,
) -> JudgeResult:
    """Judge `source` (in language `lang`) on the problem version in `problem_dir`.

    `timeline`, if given, gets the wall-clock times when the first test's box was ready (after its
    memory reservation) and when the last test finished. `on_test`, if given, is called with each
    test's result as soon as it is known (the worker posts it as progress); if it fails, judging
    carries on.

    An invalid request raises SubmissionError (or ProblemImportError for a broken problem folder).
    Anything else that goes wrong while judging (isolate failing, the disk filling up, a bug) is
    our fault, not the contestant's: it becomes a JE result instead of crashing the job.
    """
    box_id = slot.box_id
    checked = check_version(problem_dir)
    problem = checked.problem
    if lang not in problem.languages:
        raise SubmissionError(f"language {lang!r} is not allowed for this problem")
    if len(source) > MAX_SOURCE_BYTES:
        raise SubmissionError(f"source is larger than {MAX_SOURCE_BYTES} bytes")
    language = LANGUAGES[lang]

    try:
        compiled = compile_submission(slot, language, source, problem, problem_dir)
    except IsolateError as error:
        return _judge_error(problem, box_id, f"compile: {error}")
    except Exception as error:
        return _judge_error(problem, box_id, f"compile: {_internal(error)}")
    if compiled.executable is None:
        return JudgeResult(
            verdict="CE", score=0, max_score=problem.max_score(), compile_log=compiled.log,
            box_id=box_id,
        )  # fmt: skip

    try:
        program = Program(
            language=language,
            executable_name=compiled.executable_name,
            executable=compiled.executable,
            limits=run_limits(problem, lang, time_scale),
            checker=checkers.prepare(problem, problem_dir, slot),
        )
    except (checkers.CheckerError, IsolateError) as error:
        tests = [_test_error(name, str(error)) for name in checked.test_names]
        return _summarise(problem, checked.test_names, tests, compiled.log, box_id)
    except Exception as error:
        tests = [_test_error(name, _internal(error)) for name in checked.test_names]
        return _summarise(problem, checked.test_names, tests, compiled.log, box_id)
    tests = []
    for name in checked.test_names:
        try:
            tests.append(run_test(problem, problem_dir, name, program, slot, timeline))
        except IsolateError as error:
            tests.append(_test_error(name, str(error)))
        except Exception as error:
            tests.append(_test_error(name, _internal(error)))
        if on_test is not None:
            try:
                on_test(tests[-1])
            except Exception:  # noqa: S110 - progress is only a nicety; the final result counts
                pass
    if timeline is not None:
        timeline["run_finished"] = time.time()
    return _summarise(problem, checked.test_names, tests, compiled.log, box_id)


def run_limits(problem: Problem, lang: str, time_scale: float) -> Limits:
    """Run limits: CPU = TL x time_scale x language factor, wall = 2 x that + 1 s."""
    factor = next((f for key, f in problem.lang_time_factor.items() if key == lang), 1.0)
    cpu = problem.time_limit_ms / 1000 * time_scale * factor
    memory_kb = problem.memory_limit_mb * 1024
    return Limits(
        cpu_time=round(cpu, 3), wall_time=round(2 * cpu + 1, 3), extra_time=0.5,
        memory_kb=memory_kb, processes=1, fsize_kb=limits.OUTPUT_FSIZE_KB, stack_kb=memory_kb,
    )  # fmt: skip


def compile_submission(
    slot: Slot, language: Language, source: bytes, problem: Problem, problem_dir: Path
) -> Compiled:
    """Compile in a fresh box (CMS commands); return the executable's bytes or the error log.

    With a grader (cms/grading/tasktypes/Batch.py, compile): the submission is saved as
    `submission_file` with this language's extension, the grader comes first on the command line,
    headers are copied alongside, and the executable is named after the submission.
    """
    files: dict[str, bytes] = {}
    sources: list[str] = []
    basename = problem.executable_basename()
    if problem.grader is not None:
        grader_files = problem.grader.files.items()
        for path in next((paths for key, paths in grader_files if key == language.key), []):
            name = Path(path).name
            files[name] = (problem_dir / path).read_bytes()
            if name.endswith(language.source_extension):
                sources.append(name)
    source_name = basename + language.source_extension
    files[source_name] = source
    sources.append(source_name)
    executable = language.executable_name(basename)
    logs = []
    with slot.box(limits.COMPILE) as box:
        for name, data in files.items():
            box.put(name, data)
        for command in language.compile_commands(sources, executable):
            result = box.run(command, limits.COMPILE, stdout=_STDOUT, stderr=_STDERR)
            logs.append(
                _text(box.get(_STDOUT, MAX_LOG_BYTES)) + _text(box.get(_STDERR, MAX_LOG_BYTES))
            )
            if result.meta.status == "XX":
                raise IsolateError(result.meta.message)
            if result.meta.status is not None:
                status = {"TO": "compilation timed out", "SG": "the compiler crashed"}
                logs.append(status.get(result.meta.status, ""))
                return Compiled(None, executable, _cap_log(logs))
        binary = box.get(executable, MAX_EXECUTABLE_BYTES + 1)
    if binary is None or len(binary) > MAX_EXECUTABLE_BYTES:
        return Compiled(None, executable, _cap_log([*logs, "no executable was produced"]))
    return Compiled(binary, executable, _cap_log(logs))


def run_test(
    problem: Problem,
    problem_dir: Path,
    name: str,
    program: Program,
    slot: Slot,
    timeline: dict[str, float] | None = None,
) -> TestResult:
    """Run one test in a fresh box, then check its output (in another fresh box).

    `timeline` gets "run_started" when the first test's box is ready, after its memory is reserved.
    """
    test_input = (problem_dir / "tests" / f"{name}.in").read_bytes()
    correct = (problem_dir / "tests" / f"{name}.out").read_bytes()
    input_name = problem.io.input or _STDIN
    output_name = problem.io.output or _STDOUT
    with slot.box(program.limits) as box:
        if timeline is not None:
            timeline.setdefault("run_started", time.time())
        box.put(program.executable_name, program.executable, executable=True)
        box.put(input_name, test_input)
        result = box.run(
            program.language.run_command(program.executable_name),
            program.limits,
            stdin=None if problem.io.input else _STDIN,
            stdout=_STDOUT,
            stderr=_STDERR,
        )
        output_bytes = box.size(output_name)
        output = box.get(output_name, program.limits.fsize_kb * 1024 + 1)
    meta = result.meta
    memory = meta.cg_mem_kb if meta.cg_mem_kb is not None else (meta.max_rss_kb or 0)
    common = {"name": name, "time": meta.time, "wall_time": meta.wall_time, "memory_kb": memory}
    run = program.limits
    verdict = run_verdict(meta, run.memory_kb, output_bytes, run.fsize_kb)
    if verdict is not None:
        return TestResult(verdict=verdict.value, outcome=0.0, **common)
    check = program.checker.check(slot, test_input, correct, output)
    if check.outcome is None:
        return TestResult(verdict="JE", outcome=0.0, message=_message(check.message), **common)
    return TestResult(
        verdict=outcome_verdict(check.outcome).value,
        outcome=check.outcome,
        message=_message(check.message),
        **common,
    )


def _summarise(
    problem: Problem, test_names: list[str], tests: list[TestResult], log: str, box_id: int
) -> JudgeResult:
    outcomes = {t.name: t.outcome for t in tests if t.verdict in ("AC", "PA")}
    scored = score(problem.scoring, test_names, outcomes)
    verdicts = [t.verdict for t in tests]
    if "JE" in verdicts:
        overall = "JE"
    elif all(v == "AC" for v in verdicts):
        overall = "AC"
    else:
        overall = next(v for v in verdicts if v != "AC")
    return JudgeResult(
        verdict=overall,
        score=scored.score,
        max_score=problem.max_score(),
        compile_log=log,
        tests=tests,
        subtasks=[
            SubtaskScore(name=s.name, points=s.points, score=s.score) for s in scored.subtasks
        ],
        max_time=max((t.time for t in tests), default=0.0),
        max_memory_kb=max((t.memory_kb for t in tests), default=0),
        box_id=box_id,
    )


def _judge_error(problem: Problem, box_id: int, message: str) -> JudgeResult:
    return JudgeResult(
        verdict="JE",
        score=0,
        max_score=problem.max_score(),
        box_id=box_id,
        message=_message(message),
    )


def _test_error(name: str, message: str) -> TestResult:
    return TestResult(
        name=name, verdict=Verdict.JE.value, outcome=0.0, time=0.0, wall_time=0.0, memory_kb=0,
        message=_message(message),
    )  # fmt: skip


def _text(data: bytes | None) -> str:
    return "" if data is None else data.decode("utf-8", "replace")


def _cap_log(parts: list[str]) -> str:
    """Join and clean the log, then cut it to MAX_LOG_BYTES of UTF-8 (never mid-character)."""
    encoded = _clean("".join(parts)).encode("utf-8")[:MAX_LOG_BYTES]
    return encoded.decode("utf-8", "ignore")


def _internal(error: Exception) -> str:
    """Describe an unexpected error for a JE message."""
    return f"internal error: {type(error).__name__}: {error}"


def _message(text: str) -> str:
    """A message for the result: cleaned and at most MAX_MESSAGE_CHARS long."""
    return _clean(text[: MAX_MESSAGE_CHARS * 2])[:MAX_MESSAGE_CHARS]


def _clean(text: str) -> str:
    """Drop control and format characters (keeping newlines and tabs) from text out of a box.

    Control characters could drive a terminal (ANSI escapes, C1 CSI), and format characters like
    U+202E or U+2066 could make a message display differently from what it says.
    """
    return "".join(
        char
        for char in text
        if char in "\t\n" or unicodedata.category(char) not in _DROPPED_CATEGORIES
    )
