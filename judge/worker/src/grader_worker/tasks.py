"""Jobs that aren't one submission: validating and timing a problem version's reference solutions.

Each reference solution is judged exactly like a submission (same sandbox), one after the other in
the job's slot. The results go back to web-admin as a report.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grader_common.api import (
    CalibrationReport,
    CalibrationSolution,
    CalibrationTest,
    JudgeResult,
    SolutionRun,
    ValidationReport,
    VerdictCode,
)
from grader_common.importer.native import check_version
from grader_worker.judge import MAX_SOURCE_BYTES, SubmissionError, run_limits

JudgeFn = Callable[..., JudgeResult]

# Scores are sums of floats; "got the expected score" allows for rounding.
SCORE_TOLERANCE = 1e-6
MAX_MESSAGE = 4096
# Calibration runs each solution this many times, with limits this many times the contest's, so
# a slow judge CPU shows up as a long time instead of a cut-off TLE.
CALIBRATION_RUNS = 5
CALIBRATION_TIME_SCALE = 10.0


def validate(
    problem_dir: Path, slot: Any, time_scale: float, judge_fn: JudgeFn
) -> ValidationReport:
    """Judge every reference solution in `problem.yaml`; each must get its `expected_score`.

    A solution without an `expected_score` only has to compile and be judged (no CE, no JE).
    """
    problem = check_version(problem_dir).problem
    runs = []
    for solution in problem.solutions:
        try:
            source = _read_source(problem_dir / solution.file)
            result = judge_fn(problem_dir, solution.lang, source, slot, time_scale)
        except (SubmissionError, OSError) as error:
            runs.append(
                SolutionRun(
                    file=solution.file, lang=solution.lang, expected_score=solution.expected_score,
                    verdict="JE", score=0, max_score=problem.max_score(), ok=False,
                    message=str(error)[:MAX_MESSAGE],
                )
            )  # fmt: skip
            continue
        ok, message = _judge_outcome(solution.expected_score, result)
        runs.append(
            SolutionRun(
                file=solution.file, lang=solution.lang, expected_score=solution.expected_score,
                verdict=result.verdict, score=result.score, max_score=result.max_score, ok=ok,
                message=message[:MAX_MESSAGE],
            )
        )  # fmt: skip
    warnings = [] if problem.solutions else ["no reference solutions to check"]
    return ValidationReport(kind="validate", solutions=runs, warnings=warnings)


def _read_source(path: Path) -> bytes:
    """A reference solution from the (read-only, importer-checked) problem store.

    O_NOFOLLOW: the importer never stores symlinks, and the worker doesn't follow one either.
    """
    # O_NONBLOCK: opening a FIFO would otherwise wait forever (the importer never stores one).
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as file:
        data = file.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise SubmissionError(f"source is larger than {MAX_SOURCE_BYTES} bytes")
    return data


def _judge_outcome(expected: float | None, result: JudgeResult) -> tuple[bool, str]:
    """Whether the result is what the problem says, and a message saying why not."""
    if result.verdict == "CE":
        return False, result.compile_log or "compile error"
    if result.verdict == "JE":
        return False, result.message or "judge error"
    if expected is None:
        return True, ""
    if abs(result.score - expected) <= SCORE_TOLERANCE:
        return True, ""
    return False, f"expected {expected:g}, got {result.score:g}"


def calibrate(
    problem_dir: Path, slot: Any, time_scale: float, judge_fn: JudgeFn
) -> CalibrationReport:
    """Run every reference solution CALIBRATION_RUNS times; keep each test's slowest CPU time.

    `time_scale` (the site's current one) is not used: the runs use CALIBRATION_TIME_SCALE.
    """
    del time_scale
    problem = check_version(problem_dir).problem
    solutions = []
    for solution in problem.solutions:
        source = _read_source(problem_dir / solution.file)
        slowest: dict[str, float] = {}
        accepted: dict[str, bool] = {}
        verdicts: list[VerdictCode] = []
        for _ in range(CALIBRATION_RUNS):
            result = judge_fn(problem_dir, solution.lang, source, slot, CALIBRATION_TIME_SCALE)
            verdicts.append(result.verdict)
            for test in result.tests:
                slowest[test.name] = max(slowest.get(test.name, 0.0), test.time)
                accepted[test.name] = accepted.get(test.name, True) and test.verdict == "AC"
        limit = run_limits(problem, solution.lang, 1.0).cpu_time
        tests = [
            CalibrationTest(name=name, max_time=time, accepted=accepted[name])
            for name, time in slowest.items()
        ]
        solutions.append(
            CalibrationSolution(
                file=solution.file, lang=solution.lang, limit=limit, tests=tests, verdicts=verdicts
            )
        )
    warnings = [] if problem.solutions else ["no reference solutions to time"]
    return CalibrationReport(
        kind="calibrate", runs=CALIBRATION_RUNS, solutions=solutions, warnings=warnings
    )
