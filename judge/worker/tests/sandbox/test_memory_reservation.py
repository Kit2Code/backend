"""Concurrent jobs share JUDGE_MEM_TOTAL without false MLE (real worker container).

Each job uses about 480 MB under a 512 MB limit. Under the dev ceiling of 1024 MB two such jobs fit
side by side. With JUDGE_MEM_TOTAL=768M given to judge-run (the cgroup ceiling stays 1 GiB, so this
is safe) they must not overlap: the second waits for the first's reservation to be released.
"""

import json
import subprocess
from typing import Any

from conftest import COMPOSE, FIXTURES_IN_WORKER

PROBLEM = f"{FIXTURES_IN_WORKER}/judge/big-memory"
SOURCE = f"{PROBLEM}/solutions/touch480.c"


def run_two_jobs(mem_total: str) -> list[dict[str, Any]]:
    jobs = json.dumps([{"problem": PROBLEM, "lang": "c11", "source": SOURCE}] * 2)
    command = [*COMPOSE, "exec", "-T", "-e", f"JUDGE_MEM_TOTAL={mem_total}", "worker"]
    command += ["judge-run", "--jobs", "-"]
    completed = subprocess.run(  # noqa: S603 - fixed argv list, no shell
        command, input=jobs, capture_output=True, text=True, timeout=300, check=False
    )
    assert completed.returncode == 0, completed.stderr
    results = json.loads(completed.stdout)
    assert isinstance(results, list) and len(results) == 2
    return results


def overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Seconds during which both jobs were running their tests."""
    finished = min(float(a["run_finished"]), float(b["run_finished"]))
    return finished - max(float(a["run_started"]), float(b["run_started"]))


def test_two_512mb_jobs_under_1024mb_run_together_without_false_mle() -> None:
    first, second = run_two_jobs("1024M")
    for job in (first, second):
        assert job["result"]["verdict"] == "AC", job
        assert job["result"]["max_memory_kb"] > 400 * 1024
    assert first["box_id"] != second["box_id"]
    assert overlap(first, second) > 0.2


def test_with_768mb_the_second_job_waits_for_memory() -> None:
    first, second = run_two_jobs("768M")
    for job in (first, second):
        assert job["result"]["verdict"] == "AC", job
    assert overlap(first, second) <= 0
