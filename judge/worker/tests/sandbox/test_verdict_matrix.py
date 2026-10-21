"""judge-run gives every verdict in every language (real worker container, real isolate).

The problem is fixtures/judge/sum-product: TL 1 s, ML 256 MB, one test, and a CMS-style checker that
gives half of the points for each of the two answers, so partial credit (PA) can be reached too.

The memory limit is well above the 64 MB output limit on purpose: the page cache of the output
file the program writes is charged to the box's memory cgroup, so under a 64 MB memory limit an
output flood is killed for memory before it reaches the output limit.
"""

import pytest
from conftest import FIXTURES_IN_WORKER, judge

PROBLEM = f"{FIXTURES_IN_WORKER}/judge/sum-product"
PROGRAMS = f"{FIXTURES_IN_WORKER}/submissions/verdicts"
TIME_LIMIT = 1.0  # seconds, from the problem's time_limit_ms
MEMORY_LIMIT_KB = 256 * 1024

EXTENSIONS = {"c11": ".c", "cpp17": ".cpp", "cpp20": ".cpp", "python3": ".py"}
LANGS = list(EXTENSIONS)
RUN_VERDICTS = ["AC", "PA", "WA", "TLE", "MLE", "RE", "OLE"]


def judge_program(lang: str, verdict: str) -> dict:  # type: ignore[type-arg]
    return judge(PROBLEM, lang, f"{PROGRAMS}/{verdict.lower()}{EXTENSIONS[lang]}")


# What the checker says for the runs it sees: (outcome, message).
CHECKED = {
    "AC": (1.0, "translate:success"),
    "PA": (0.5, "translate:partial"),
    "WA": (0.0, "translate:wrong"),
}


@pytest.mark.parametrize("verdict", RUN_VERDICTS)
@pytest.mark.parametrize("lang", LANGS)
def test_run_verdict(lang: str, verdict: str) -> None:
    result = judge_program(lang, verdict)
    assert [t["verdict"] for t in result["tests"]] == [verdict], result
    assert result["verdict"] == verdict, result
    expected_score = {"AC": 100, "PA": 50}.get(verdict, 0)
    assert (result["score"], result["max_score"]) == (expected_score, 100), result
    assert 100 <= result["box_id"] <= 109
    test = result["tests"][0]
    if verdict in CHECKED:
        assert (test["outcome"], test["message"]) == CHECKED[verdict], test
    else:
        assert test["outcome"] == 0.0, test
    if verdict == "TLE":
        # Stopped after using the time limit, in less than 2 x TL of wall time. These are
        # isolate's own measurements of the run (its meta file), so compiling, checking and
        # waiting for a judge slot don't count.
        assert test["time"] >= TIME_LIMIT, test
        assert test["wall_time"] <= 2 * TIME_LIMIT, test
    if verdict == "MLE":
        assert test["memory_kb"] >= MEMORY_LIMIT_KB * 0.9, test


@pytest.mark.parametrize("lang", LANGS)
def test_compile_error(lang: str) -> None:
    result = judge_program(lang, "CE")
    assert result["verdict"] == "CE", result
    assert result["score"] == 0
    assert result["tests"] == []
    assert result["compile_log"].strip(), "the compiler's messages are shown"
