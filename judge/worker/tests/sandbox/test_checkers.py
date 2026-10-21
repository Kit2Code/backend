"""CMS-style and testlib checkers, compiled and run inside the sandbox (real worker container)."""

import pytest
from conftest import FIXTURES_IN_WORKER, judge, put_source, worker

PROBLEMS = f"{FIXTURES_IN_WORKER}/problems"
JUDGE = f"{FIXTURES_IN_WORKER}/judge"


def say(token: str) -> str:
    return put_source(f"say_{token}.py", f"print({token!r})\n")


# --- CMS-style checker: checker <input> <correct output> <contestant output> ----------------


def test_cms_checker_full_and_partial_points() -> None:
    good = judge(f"{PROBLEMS}/cms-checker", "cpp17", f"{PROBLEMS}/cms-checker/solutions/good.cpp")
    assert good["verdict"] == "AC" and good["score"] == 100
    trivial = judge(
        f"{PROBLEMS}/cms-checker", "python3", f"{PROBLEMS}/cms-checker/solutions/trivial.py"
    )
    assert trivial["score"] == 50
    assert {t["verdict"] for t in trivial["tests"]} == {"PA"}
    assert {t["outcome"] for t in trivial["tests"]} == {0.5}
    assert {t["message"] for t in trivial["tests"]} == {"translate:partial"}


@pytest.mark.parametrize(
    ("token", "verdict", "outcome"),
    [("ok", "AC", 1.0), ("half", "PA", 0.5), ("nope", "WA", 0.0)],
)
def test_cms_checker_outcomes(token: str, verdict: str, outcome: float) -> None:
    result = judge(f"{JUDGE}/cms-codes", "python3", say(token))
    assert (result["tests"][0]["verdict"], result["tests"][0]["outcome"]) == (verdict, outcome)


@pytest.mark.parametrize("token", ["big", "garbage", "crash"])
def test_a_misbehaving_cms_checker_is_a_judge_error(token: str) -> None:
    result = judge(f"{JUDGE}/cms-codes", "python3", say(token))
    assert result["tests"][0]["verdict"] == "JE", result
    assert result["verdict"] == "JE"


def test_no_output_is_wrong_without_asking_the_checker() -> None:
    silent = put_source("silent.py", "pass\n")
    result = judge(f"{JUDGE}/cms-codes", "python3", silent)
    assert result["tests"][0]["verdict"] == "WA"


# --- testlib checker: checker <input> <contestant output> <answer> ----------------------------


def test_testlib_checker_accepts_any_valid_answer() -> None:
    problem = f"{PROBLEMS}/testlib-checker"
    for lang, solution in (("python3", "perm.py"), ("cpp17", "reverse.cpp")):
        result = judge(problem, lang, f"{problem}/solutions/{solution}")
        assert result["verdict"] == "AC", result


@pytest.mark.parametrize(
    ("token", "verdict"),
    [("ok", "AC"), ("wrong", "WA"), ("pe", "WA"), ("dirt", "WA"), ("fail", "JE"), ("points", "JE")],
)
def test_testlib_exit_codes(token: str, verdict: str) -> None:
    result = judge(f"{JUDGE}/testlib-codes", "python3", say(token))
    assert result["tests"][0]["verdict"] == verdict, result


def test_testlib_partial_points_are_reported_as_not_supported() -> None:
    result = judge(f"{JUDGE}/testlib-codes", "python3", say("points"))
    assert "partial points" in result["tests"][0]["message"]


# --- compiled once, cached ----------------------------------------------------------------------


def test_checkers_are_cached_after_the_first_compile() -> None:
    judge(f"{JUDGE}/cms-codes", "python3", say("ok"))
    listing = worker("find", "/data/cache/checkers", "-name", "checker", "-type", "f")
    assert listing.returncode == 0
    assert "/data/cache/checkers/cms-codes/" in listing.stdout
