"""The self-test's case table and runner, with a fake judge (the sandbox test runs it for real)."""

from pathlib import Path
from typing import Any

import pytest

from grader_common.api import JudgeResult
from grader_worker import selftest
from grader_worker.judge import SubmissionError

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "fixtures"


def result(verdict: str) -> JudgeResult:
    return JudgeResult.model_validate({"verdict": verdict, "score": 0, "max_score": 100})


IP = "10.1.2.3"


def source_of(case: selftest.Case) -> bytes:
    return (FIXTURES / case.source).read_bytes().replace(b"WEB_ADMIN_IP", IP.encode())


def expected_judge(seen: list[dict[str, Any]]) -> Any:
    """A judge that answers every case with the first verdict the table allows."""

    def judge(problem_dir: Path, lang: str, source: bytes, slot: Any, time_scale: float,
              timeline: Any = None, on_test: Any = None) -> JudgeResult:  # fmt: skip
        seen.append({"problem": problem_dir, "lang": lang, "source": source, "scale": time_scale})
        case = next(c for c in selftest.CASES if source_of(c) == source and c.lang == lang)
        assert problem_dir == FIXTURES / case.problem
        return result(case.allowed[0])

    return judge


def test_every_case_names_an_existing_fixture_and_problem() -> None:
    assert len(selftest.CASES) >= 20
    for case in selftest.CASES:
        assert (FIXTURES / case.source).is_file(), case.source
        assert (FIXTURES / case.problem / "problem.yaml").is_file(), case.problem
        assert case.allowed, case.name
    names = [case.name for case in selftest.CASES]
    assert len(names) == len(set(names))


def test_the_table_covers_every_hostile_program() -> None:
    hostile = {
        p.name for p in (FIXTURES / "hostile").iterdir() if p.suffix in (".c", ".cpp", ".py")
    }
    covered = {Path(case.source).name for case in selftest.CASES}
    assert hostile <= covered, hostile - covered


def test_a_contained_judge_passes_every_case() -> None:
    seen: list[dict[str, Any]] = []
    report = selftest.run(FIXTURES, slot=None, judge_fn=expected_judge(seen), web_admin_ip=IP)
    assert report.kind == "selftest"
    assert report.ok
    assert [case.name for case in report.cases] == [case.name for case in selftest.CASES]
    assert all(entry["scale"] == 1.0 for entry in seen)  # the real limits, not the site's scale
    assert not any(b"WEB_ADMIN_IP" in entry["source"] for entry in seen)
    assert any(IP.encode() in entry["source"] for entry in seen)


def test_a_wrong_verdict_fails_that_case_only() -> None:
    seen: list[dict[str, Any]] = []
    honest = expected_judge(seen)

    def leaky(
        problem_dir: Path, lang: str, source: bytes, *args: Any, **kwargs: Any
    ) -> JudgeResult:
        if source == (FIXTURES / "hostile" / "read_secrets.c").read_bytes():
            return result("WA")  # read_secrets printed something it shouldn't have read
        return honest(problem_dir, lang, source, *args, **kwargs)  # type: ignore[no-any-return]

    report = selftest.run(FIXTURES, slot=None, judge_fn=leaky, web_admin_ip=IP)
    assert not report.ok
    failed = [case for case in report.cases if not case.ok]
    assert [case.name for case in failed] == ["read_secrets"]
    assert failed[0].verdict == "WA" and failed[0].expected == ["AC"]


def test_a_judge_that_raises_fails_the_case_with_its_message() -> None:
    def broken(*args: Any, **kwargs: Any) -> JudgeResult:
        raise SubmissionError("language 'c11' is not allowed")

    report = selftest.run(FIXTURES, slot=None, judge_fn=broken, web_admin_ip=IP)
    assert not report.ok
    assert all("not allowed" in case.message for case in report.cases)


def test_missing_fixtures_are_an_error_not_a_pass(tmp_path: Path) -> None:
    with pytest.raises(SubmissionError, match="fixtures"):
        selftest.run(tmp_path, slot=None, judge_fn=expected_judge([]), web_admin_ip=IP)
