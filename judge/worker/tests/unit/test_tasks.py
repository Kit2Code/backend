"""Jobs other than judging one submission: validating a problem version's reference solutions.

The real judge is replaced by a fake that answers per source file, so no isolate is needed.
"""

import shutil
from pathlib import Path
from typing import Any

import pytest

from grader_common.api import JudgeResult
from grader_worker import tasks
from grader_worker.judge import SubmissionError

REPO = Path(__file__).resolve().parents[4]
GROUPMIN = REPO / "fixtures" / "problems" / "groupmin"


@pytest.fixture
def problem(tmp_path: Path) -> Path:
    folder = tmp_path / "groupmin" / "v001-0123abcd"
    shutil.copytree(GROUPMIN, folder)
    return folder


def fake_judge(scores: dict[str, tuple[str, float]]) -> Any:
    """A judge that gives each source (by its first line) a fixed verdict and score."""

    def judge(problem_dir: Path, lang: str, source: bytes, slot: Any, time_scale: float,
              timeline: Any = None, on_test: Any = None) -> JudgeResult:  # fmt: skip
        for marker, (verdict, score) in scores.items():
            if marker.encode() in source:
                log = "a.cpp:1: error" if verdict == "CE" else ""
                return JudgeResult(verdict=verdict, score=score, max_score=100, compile_log=log)
        raise AssertionError(f"unexpected source {source[:40]!r}")

    return judge


def write_solutions(problem: Path, full: str, partial: str) -> None:
    (problem / "solutions" / "full.cpp").write_text(f"// {full}\n", encoding="utf-8")
    (problem / "solutions" / "partial.cpp").write_text(f"// {partial}\n", encoding="utf-8")


def test_every_solution_that_gets_its_expected_score_passes(problem: Path) -> None:
    write_solutions(problem, "FULL", "PART")
    judge = fake_judge({"FULL": ("AC", 100), "PART": ("PA", 30)})
    report = tasks.validate(problem, slot=None, time_scale=1.0, judge_fn=judge)
    assert report.kind == "validate"
    assert [(s.file, s.lang, s.expected_score, s.score, s.ok) for s in report.solutions] == [
        ("solutions/full.cpp", "cpp17", 100, 100, True),
        ("solutions/partial.cpp", "cpp17", 30, 30, True),
    ]
    assert report.ok


def test_a_solution_with_another_score_fails_with_a_clear_message(problem: Path) -> None:
    write_solutions(problem, "FULL", "PART")
    judge = fake_judge({"FULL": ("WA", 30), "PART": ("PA", 30)})
    report = tasks.validate(problem, slot=None, time_scale=1.0, judge_fn=judge)
    assert not report.ok
    full = report.solutions[0]
    assert (full.ok, full.verdict, full.score) == (False, "WA", 30)
    assert "expected 100" in full.message and "got 30" in full.message


def test_without_an_expected_score_a_solution_only_has_to_run(problem: Path) -> None:
    text = (problem / "problem.yaml").read_text(encoding="utf-8")
    text = text.replace(", expected_score: 30}", "}")
    (problem / "problem.yaml").write_text(text, encoding="utf-8")
    write_solutions(problem, "FULL", "PART")
    judge = fake_judge({"FULL": ("AC", 100), "PART": ("TLE", 0)})
    assert tasks.validate(problem, slot=None, time_scale=1.0, judge_fn=judge).ok
    judge = fake_judge({"FULL": ("AC", 100), "PART": ("CE", 0)})
    report = tasks.validate(problem, slot=None, time_scale=1.0, judge_fn=judge)
    assert not report.ok
    assert "a.cpp:1: error" in report.solutions[1].message


def test_a_solution_the_judge_refuses_fails(problem: Path) -> None:
    write_solutions(problem, "FULL", "PART")

    def refusing(*args: Any, **kwargs: Any) -> JudgeResult:
        raise SubmissionError("source is larger than 65536 bytes")

    report = tasks.validate(problem, slot=None, time_scale=1.0, judge_fn=refusing)
    assert not report.ok
    assert "larger than" in report.solutions[0].message


def test_no_reference_solutions_is_valid_with_a_warning(problem: Path) -> None:
    text = (problem / "problem.yaml").read_text(encoding="utf-8")
    text = text[: text.index("solutions:")] + "license: CC0-1.0\n"
    (problem / "problem.yaml").write_text(text, encoding="utf-8")
    report = tasks.validate(problem, slot=None, time_scale=1.0, judge_fn=fake_judge({}))
    assert report.ok
    assert report.solutions == []
    assert report.warnings == ["no reference solutions to check"]


# ---- calibration ----


def timed_judge(times: dict[str, list[float]], calls: list[dict[str, Any]]) -> Any:
    """A judge whose every test of a source takes the next time from `times` (per first line)."""

    def judge(problem_dir: Path, lang: str, source: bytes, slot: Any, time_scale: float,
              timeline: Any = None, on_test: Any = None) -> JudgeResult:  # fmt: skip
        marker = source.decode().splitlines()[0].removeprefix("// ")
        calls.append({"marker": marker, "time_scale": time_scale})
        took = times[marker].pop(0)
        tests = [
            {"name": name, "verdict": "AC", "outcome": 1, "time": took + i / 100,
             "wall_time": took, "memory_kb": 1000}
            for i, name in enumerate(["1-01", "2-01"])
        ]  # fmt: skip
        return JudgeResult.model_validate(
            {"verdict": "AC", "score": 100, "max_score": 100, "tests": tests}
        )

    return judge


def test_calibration_runs_each_solution_five_times_without_cutting_it_off(problem: Path) -> None:
    write_solutions(problem, "FULL", "PART")
    calls: list[dict[str, Any]] = []
    times = {"FULL": [0.1, 0.5, 0.2, 0.3, 0.4], "PART": [0.05] * 5}
    report = tasks.calibrate(problem, slot=None, time_scale=1.0, judge_fn=timed_judge(times, calls))
    assert report.kind == "calibrate" and report.runs == 5
    assert len(calls) == 10
    assert {call["time_scale"] for call in calls} == {tasks.CALIBRATION_TIME_SCALE}
    full = report.solutions[0]
    assert (full.file, full.lang, full.limit) == ("solutions/full.cpp", "cpp17", 1.0)
    assert [(t.name, t.max_time) for t in full.tests] == [("1-01", 0.5), ("2-01", 0.51)]
    assert full.verdicts == ["AC"] * 5


def test_calibration_without_solutions_warns(problem: Path) -> None:
    text = (problem / "problem.yaml").read_text(encoding="utf-8")
    (problem / "problem.yaml").write_text(
        text[: text.index("solutions:")] + "license: CC0-1.0\n", encoding="utf-8"
    )
    report = tasks.calibrate(problem, slot=None, time_scale=1.0, judge_fn=fake_judge({}))
    assert report.solutions == []
    assert report.warnings == ["no reference solutions to time"]


def test_calibration_marks_tests_that_were_not_accepted_in_every_run(problem: Path) -> None:
    write_solutions(problem, "FULL", "PART")
    calls: list[dict[str, Any]] = []
    times = {"FULL": [0.1] * 5, "PART": [0.1] * 5}
    judge = timed_judge(times, calls)

    def partly_tle(*args: Any, **kwargs: Any) -> JudgeResult:
        result: JudgeResult = judge(*args, **kwargs)
        if b"PART" in args[2] and len(calls) == 8:  # one run of PART: its second test hits TLE
            tests = [result.tests[0], result.tests[1].model_copy(update={"verdict": "TLE"})]
            return result.model_copy(update={"tests": tests})
        return result

    report = tasks.calibrate(problem, slot=None, time_scale=1.0, judge_fn=partly_tle)
    full, part = report.solutions
    assert [t.accepted for t in full.tests] == [True, True]
    assert [t.accepted for t in part.tests] == [True, False]
