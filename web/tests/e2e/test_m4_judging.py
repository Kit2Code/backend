"""Submissions judged for real: browser → public site → queue → worker (isolate) → live result."""

import re
from pathlib import Path

from playwright.sync_api import Locator, Page, expect

from tests.e2e.conftest import (
    PUBLIC_URL,
    REPO,
    SCREENSHOTS,
    import_into_dev_stack,
    sign_in_new_account,
)

PROBLEMS = REPO / "fixtures" / "problems"
JUDGED_WITHIN_MS = 60_000


def submit(page: Page, slug: str, language: str, source: str) -> Locator:
    """Sign in as someone new, submit, and return the live results section."""
    page.goto(f"{PUBLIC_URL}/problems/{slug}")
    sign_in_new_account(page)
    page.locator(f'a[href="/problems/{slug}/submit"]').click()
    page.locator('select[name="language"]').select_option(language)
    page.locator('textarea[name="source"]').fill(source)
    page.locator("main form button[type=submit]").click()
    expect(page).to_have_url(re.compile(rf"^{PUBLIC_URL}/submissions/\d+$"))
    return page.locator("#results")


def finished(results: Locator) -> None:
    """Wait until the result is in and polling has stopped."""
    expect(results).not_to_have_attribute("hx-get", re.compile(".*"), timeout=JUDGED_WITHIN_MS)


def solution(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cpp17_aplusb_accepted(page: Page) -> None:
    slug = import_into_dev_stack(PROBLEMS / "aplusb")
    results = submit(page, slug, "cpp17", solution(PROBLEMS / "aplusb" / "solutions" / "main.cpp"))
    finished(results)
    expect(results).to_contain_text("ผ่าน")
    expect(results).to_contain_text("คะแนน: 100")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / "m4-aplusb-ac.png", full_page=True)


def test_groupmin_partial_shows_subtask_scores(page: Page) -> None:
    slug = import_into_dev_stack(PROBLEMS / "groupmin")
    source = solution(PROBLEMS / "groupmin" / "solutions" / "partial.cpp")
    results = submit(page, slug, "cpp17", source)
    finished(results)
    # The overall verdict is the first failing test's (here a wrong answer); the score is partial.
    expect(results).to_contain_text("คำตอบผิด · คะแนน: 30")
    rows = results.locator("table").first.locator("tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator("td")).to_have_text(["1", "30 / 30"])
    expect(rows.nth(1).locator("td")).to_have_text(["2", "0 / 70"])
    page.screenshot(path=SCREENSHOTS / "m4-groupmin-partial.png", full_page=True)


def test_compile_error_shows_the_compiler_log(page: Page) -> None:
    slug = import_into_dev_stack(PROBLEMS / "aplusb")
    results = submit(page, slug, "cpp17", "int main( {\n")
    finished(results)
    expect(results).to_contain_text("คอมไพล์ไม่ผ่าน")
    expect(results.locator("pre")).to_contain_text("error")
    page.screenshot(path=SCREENSHOTS / "m4-compile-error.png", full_page=True)


def test_python_aplusb_accepted(page: Page) -> None:
    slug = import_into_dev_stack(PROBLEMS / "aplusb")
    results = submit(page, slug, "python3", solution(PROBLEMS / "aplusb" / "solutions" / "main.py"))
    finished(results)
    expect(results).to_contain_text("ผ่าน")
    expect(results).to_contain_text("คะแนน: 100")
