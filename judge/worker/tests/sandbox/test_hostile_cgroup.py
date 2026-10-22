"""A program in a box can't leave its cgroup or change any cgroup limit (fixtures/hostile)."""

from conftest import FIXTURES_IN_WORKER, worker_json

PROBLEM = f"{FIXTURES_IN_WORKER}/hostile/problem"
TIMEOUT = 120


def test_a_box_cannot_touch_any_cgroup() -> None:
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/cgroup_escape.c", timeout=TIMEOUT,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    test = result["tests"][0]
    assert test["verdict"] == "AC", f"not contained: {test}"
