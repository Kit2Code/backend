"""Helpers for tests that drive the running dev stack's containers (start it with ./init.sh).

Like the sandbox tests, they fail when the stack is down and are skipped only when
GRADER_SKIP_SANDBOX=1, which CI sets in its plain test job.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(REPO / "deploy" / "dev" / "compose.dev.yml")]


def run_in(service: str, *argv: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command inside one of the dev stack's containers."""
    return subprocess.run(  # noqa: S603 - fixed argv list, no shell
        [*COMPOSE, "exec", "-T", service, *argv],
        capture_output=True,
        text=True,
        timeout=60,
        input=stdin,
        check=False,
    )


@pytest.fixture(autouse=True, scope="session")
def _dev_stack_is_running() -> None:
    if os.environ.get("GRADER_SKIP_SANDBOX") == "1":
        pytest.skip("GRADER_SKIP_SANDBOX=1: these tests need the dev stack")
    probe = run_in("edge", "true")
    if probe.returncode != 0:
        pytest.fail(f"the dev stack isn't running (start it with ./init.sh):\n{probe.stderr}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this folder `devstack`, so `-m "not devstack"` can leave them out."""
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.devstack)
