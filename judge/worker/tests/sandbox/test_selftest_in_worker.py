"""The self-test, run for real in the worker container: every hostile program is contained.

It judges the copy of the fixtures baked into the worker image, like on the real server.
"""

import json

from conftest import worker

# About 26 programs, several of them stopped only by the time or compile limits.
TIMEOUT = 600


def test_the_self_test_passes_in_the_worker() -> None:
    run = worker("judge-selftest", timeout=TIMEOUT)
    assert run.returncode in (0, 1), run.stderr
    report = json.loads(run.stdout)
    failed = [case for case in report["cases"] if not case["ok"]]
    assert failed == [], json.dumps(failed, indent=2)
    assert run.returncode == 0
    assert len(report["cases"]) >= 20
