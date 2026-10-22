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
from playwright.sync_api import Page, expect

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


def sign_in_new_account(page: Page) -> str:
    """Sign in with a brand-new fake Google account and pick a username; return it.

    Starts and ends on the page that is open; the site must be in Thai. Each new account comes
    from its own visitor address, so the first-sign-in limit (5 per hour per address) isn't hit.
    """
    page.set_extra_http_headers({"Cf-Connecting-Ip": f"198.51.100.{secrets.randbelow(254) + 1}"})
    page.locator("header").get_by_role("link", name="เข้าสู่ระบบด้วย Google").click()
    page.get_by_label("Other account id").fill("e2e-" + secrets.token_hex(8))
    page.get_by_role("button", name="Use it").click()
    expect(page).to_have_url(f"{PUBLIC_URL}/auth/welcome")
    username = "e2e_" + secrets.token_hex(4)
    page.get_by_label("ชื่อผู้ใช้").fill(username)
    page.get_by_label("ชื่อที่แสดง").fill("ทดสอบ อีทูอี")
    page.get_by_label("ฉันอ่านนโยบายความเป็นส่วนตัวแล้ว").check()
    page.get_by_role("button", name="สร้างบัญชีของฉัน").click()
    expect(page.locator("header")).to_contain_text(username)
    return username


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this folder `devstack`."""
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.devstack)
