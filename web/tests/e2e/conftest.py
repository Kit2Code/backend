"""End-to-end tests: a real browser (Chromium, via Playwright) against the running dev stack.

They need `./init.sh` first and Playwright's browser (`uv run playwright install chromium`; on a
fresh machine also `sudo uv run playwright install-deps chromium`). Like the sandbox tests they
fail when the stack is down, and are skipped only when GRADER_SKIP_SANDBOX=1 (CI's plain job).
"""

import os
import secrets
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
PUBLIC_URL = "http://127.0.0.1:8000"
# Screenshots land here (ignored by git), for looking at the pages after a run.
SCREENSHOTS = REPO / "test-results" / "e2e"


@pytest.fixture(autouse=True, scope="session")
def _dev_stack_is_running() -> None:
    if os.environ.get("GRADER_SKIP_SANDBOX") == "1":
        pytest.skip("GRADER_SKIP_SANDBOX=1: end-to-end tests need the dev stack")
    try:
        urllib.request.urlopen(f"{PUBLIC_URL}/healthz", timeout=5)  # noqa: S310 - fixed URL
    except OSError as error:
        pytest.fail(f"the dev stack isn't answering on {PUBLIC_URL} (run ./init.sh): {error}")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Each test run looks like a new visitor (Cloudflare's header), so rate limits don't carry
    over between runs."""
    address = f"198.51.100.{secrets.randbelow(254) + 1}"
    return {**browser_context_args, "extra_http_headers": {"Cf-Connecting-Ip": address}}


def import_into_dev_stack(source: Path) -> str:
    """Import a fixture problem into the dev stack's store and database, published; return its
    slug. Uses the developer machine's manage.py, which talks to the dev stack's Postgres."""
    result = subprocess.run(  # noqa: S603 - fixed argv list
        [sys.executable, "manage.py", "import_problem", str(source), "--visible"],
        cwd=REPO / "web",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().split("/")[0]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this folder `devstack`."""
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.devstack)
