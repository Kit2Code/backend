"""Helpers for sandbox tests: they drive the real worker container of the dev stack.

Start the stack with ./init.sh first. These tests fail (they don't skip) when the stack is down;
they are skipped only when GRADER_SKIP_SANDBOX=1, which CI sets in its plain test job.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
COMPOSE = ["docker", "compose", "-f", str(REPO / "deploy" / "dev" / "compose.dev.yml")]
# Where the repo's fixtures/ folder is mounted inside the worker container.
FIXTURES_IN_WORKER = "/app/fixtures"


def worker(
    *argv: str, timeout: float = 300, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command inside the worker container and capture its output."""
    return subprocess.run(  # noqa: S603 - fixed argv list, no shell
        [*COMPOSE, "exec", "-T", "worker", *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        input=stdin,
        check=False,
    )


def worker_json(*argv: str, timeout: float = 300) -> Any:
    """Run a worker command that prints JSON, and return the parsed result."""
    result = worker(*argv, timeout=timeout)
    assert result.returncode == 0, f"{argv} failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(str(result.stdout))


@pytest.fixture(autouse=True, scope="session")
def _dev_stack_is_running() -> None:
    if os.environ.get("GRADER_SKIP_SANDBOX") == "1":
        pytest.skip("GRADER_SKIP_SANDBOX=1: sandbox tests need the dev stack with real isolate")
    probe = worker("true", timeout=60)
    if probe.returncode != 0:
        pytest.fail(
            f"the dev stack's worker isn't running (start it with ./init.sh):\n{probe.stderr}"
        )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this folder `sandbox`, so `-m "not sandbox"` can leave them out."""
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.sandbox)


def put_source(name: str, text: str) -> str:
    """Write a test program into the worker container and return its path there."""
    path = f"/run/judge-tests/{name}"
    result = worker("sh", "-c", 'mkdir -p /run/judge-tests && cat > "$1"', "sh", path, stdin=text)
    assert result.returncode == 0, result.stderr
    return path


def judge(problem: str, lang: str, source: str, *extra: str) -> dict[str, Any]:
    """Run judge-run in the worker and return the result."""
    result = worker_json(
        "judge-run", "--problem", problem, "--lang", lang, "--source", source, *extra
    )
    assert isinstance(result, dict)
    return result
