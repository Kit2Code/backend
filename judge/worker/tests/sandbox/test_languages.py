"""Every language compiles and runs inside isolate and solves aplusb (real worker container)."""

import pytest
from conftest import FIXTURES_IN_WORKER, worker_json

APLUSB = f"{FIXTURES_IN_WORKER}/problems/aplusb"
PROGRAMS = f"{FIXTURES_IN_WORKER}/submissions/aplusb"


def judge(lang: str, source: str, problem: str = APLUSB) -> dict:  # type: ignore[type-arg]
    result = worker_json("judge-run", "--problem", problem, "--lang", lang, "--source", source)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize(
    ("lang", "source"),
    [
        ("c11", "ac.c"),
        ("cpp17", "ac.cpp"),
        ("cpp20", "ac.cpp"),
        ("cpp20", "cpp20.cpp"),
        ("python3", "ac.py"),
    ],
)
def test_accepted_in_every_language(lang: str, source: str) -> None:
    result = judge(lang, f"{PROGRAMS}/{source}")
    assert result["verdict"] == "AC", result
    assert result["score"] == 100
    assert [t["verdict"] for t in result["tests"]] == ["AC", "AC", "AC"]
    assert all(0 <= t["time"] < 1 for t in result["tests"])
    assert all(t["memory_kb"] > 0 for t in result["tests"])


def test_cpp20_code_does_not_compile_as_cpp17() -> None:
    result = judge("cpp17", f"{PROGRAMS}/cpp20.cpp")
    assert result["verdict"] == "CE", result


@pytest.mark.parametrize(
    ("lang", "source", "needle"),
    [
        ("c11", "ce.c", "undeclared_name"),
        ("cpp17", "ce.cpp", "error"),
        ("python3", "ce.py", "Syntax"),
    ],
)
def test_compile_errors_show_the_log(lang: str, source: str, needle: str) -> None:
    result = judge(lang, f"{PROGRAMS}/{source}")
    assert result["verdict"] == "CE"
    assert result["score"] == 0
    assert result["tests"] == []
    assert needle in result["compile_log"]


def test_judging_uses_one_of_our_boxes() -> None:
    result = judge("cpp17", f"{PROGRAMS}/ac.cpp")
    assert 100 <= result["box_id"] <= 109
