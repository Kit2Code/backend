"""The queue survives crashes: a killed worker's job comes back after its lease, and restarting both
web roles loses nothing (the queue lives in Postgres)."""

import re
import subprocess
import time
import urllib.request
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import PUBLIC_URL, REPO, import_into_dev_stack, sign_in_new_account

COMPOSE = ["docker", "compose", "-f", str(REPO / "deploy" / "dev" / "compose.dev.yml")]
PROBLEMS = REPO / "fixtures" / "problems"
LEASE_SECONDS = 60
# Every aplusb test runs into the 1 s time limit, so judging takes several seconds: long enough
# to kill the worker in the middle of it.
SLOW = "int main() { volatile unsigned long n = 0; for (;;) n++; }\n"


def compose(*argv: str) -> None:
    subprocess.run([*COMPOSE, *argv], check=True, capture_output=True, timeout=180)  # noqa: S603


@pytest.fixture(autouse=True)
def _worker_runs_afterwards() -> Iterator[None]:
    """Whatever happens, leave the stack with a running worker."""
    yield
    compose("start", "worker")


def submit(page: Page, source: str) -> str:
    """Sign in as someone new, submit to aplusb in C++17; return the results fragment's URL."""
    slug = import_into_dev_stack(PROBLEMS / "aplusb")
    page.goto(f"{PUBLIC_URL}/problems/{slug}")
    sign_in_new_account(page)
    page.goto(f"{PUBLIC_URL}/problems/{slug}/submit")
    page.locator('select[name="language"]').select_option("cpp17")
    page.locator('textarea[name="source"]').fill(source)
    page.locator("main form button[type=submit]").click()
    expect(page).to_have_url(re.compile(rf"^{PUBLIC_URL}/submissions/\d+$"))
    return page.url + "/results"


def fragment(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - the dev stack
        return str(response.read().decode())


def wait_for(url: str, condition: str, timeout: float) -> float:
    """Poll the results fragment until it is `judging` or `done`; return the seconds it took."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        html = fragment(url)
        if condition == "judging" and "กำลังตรวจ" in html:
            return time.monotonic() - start
        if condition == "done" and "hx-get" not in html:
            return time.monotonic() - start
        time.sleep(0.2)
    raise AssertionError(f"not {condition} after {timeout} s:\n{fragment(url)}")


def wait_until_up(url: str, timeout: float = 120) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            fragment(url)
            return
        except OSError:
            time.sleep(1)
    raise AssertionError(f"{url} didn't come back within {timeout} s")


def test_a_killed_workers_job_is_judged_after_its_lease_runs_out(page: Page) -> None:
    results = submit(page, SLOW)
    wait_for(results, "judging", timeout=30)
    compose("kill", "worker")
    killed_at = time.monotonic()
    assert "กำลังตรวจ" in fragment(results)  # still marked as judging: nobody knows yet
    compose("start", "worker")
    wait_for(results, "done", timeout=LEASE_SECONDS + 90)
    # It came back only through the expired lease (the last heartbeat was at most 10 s before
    # the kill), not because something finished the old run.
    assert time.monotonic() - killed_at >= LEASE_SECONDS - 15
    assert "เกินเวลา" in fragment(results)


def test_restarting_both_web_roles_loses_no_queued_job(page: Page) -> None:
    compose("stop", "worker")
    results = submit(page, (PROBLEMS / "aplusb" / "solutions" / "main.cpp").read_text())
    assert "รอตรวจ" in fragment(results)
    compose("restart", "web", "web-admin")
    wait_until_up(results)
    assert "รอตรวจ" in fragment(results)
    compose("start", "worker")
    wait_for(results, "done", timeout=90)
    assert "ผ่าน" in fragment(results)
