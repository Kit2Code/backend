"""A huge one-line output is compared by white-diff promptly and without a judge error.

fixtures/hostile/whitediff_bomb.c prints about 33 million "1 " tokens on one line, just under the
64 MB file-size limit, so the run ends normally and white-diff (in the worker, outside the sandbox)
has to compare it with the expected `CONTAINED`.
"""

import time

from conftest import FIXTURES_IN_WORKER, worker_json

PROBLEM = f"{FIXTURES_IN_WORKER}/hostile/problem"
# Compile + one run + the comparison. A worker that ran out of memory would fail or crawl.
PROMPT_SECONDS = 60


def test_a_huge_single_line_output_is_compared_promptly() -> None:
    started = time.monotonic()
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/whitediff_bomb.c", timeout=PROMPT_SECONDS * 2,
    )  # fmt: skip
    elapsed = time.monotonic() - started
    assert result["verdict"] != "CE", result["compile_log"]
    assert len(result["tests"]) == 1, result
    test = result["tests"][0]
    assert test["verdict"] in ("WA", "OLE", "MLE"), test
    assert elapsed < PROMPT_SECONDS, f"took {elapsed:.1f} s"
