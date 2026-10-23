"""The browser problem editor end to end, against the dev stack (real worker and isolate):
create a problem in the form → the worker validates it → open it with the Problems page switch
→ a student gets Accepted. Each run uses a fresh slug.
"""

import re
import secrets
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import PUBLIC_URL, REPO, SCREENSHOTS, sign_in_new_account
from tests.e2e.test_m5_admin import ADMIN_URL, JUDGE_TIMEOUT_MS, shell

APLUSB = REPO / "fixtures" / "problems" / "aplusb"


@pytest.fixture(scope="module")
def staff_login() -> Iterator[dict[str, str]]:
    """A staff account made for this run, removed afterwards."""
    login = {
        "username": "e2e_editor_" + secrets.token_hex(3),
        "password": secrets.token_urlsafe(16),
    }
    shell(
        "from accounts.models import User\n"
        f"User.objects.create_superuser({login['username']!r}, password={login['password']!r})"
    )
    yield login
    shell(
        "from accounts.models import User\n"
        f"User.objects.filter(username={login['username']!r}).delete()"
    )


def test_create_in_the_browser_validate_open_and_solve(
    page: Page, staff_login: dict[str, str]
) -> None:
    page.goto(f"{ADMIN_URL}/admin/login/")
    page.locator("#id_username").fill(staff_login["username"])
    page.locator("#id_password").fill(staff_login["password"])
    page.locator("input[type=submit]").click()
    expect(page.locator("#grader-pages")).to_be_visible()

    # The form: statement, tests (several files at once), scoring, checker, a reference solution.
    page.locator('.quick-actions a[href="/admin/grader/problems/new"]').click()
    slug = "e2e-ed-" + secrets.token_hex(3)
    page.locator('input[name="slug"]').fill(slug)
    page.locator('input[name="title_th"]').fill(f"E2E {slug}")
    page.locator('textarea[name="statement_th"]').fill("# บวกเลข\n\nรับ *a* และ *b* แล้วพิมพ์ a + b")
    page.get_by_role("button", name="ดูตัวอย่างโจทย์ภาษาไทย").click()
    expect(page.locator("#statement-preview h1")).to_have_text("บวกเลข")
    page.locator('input[name="tests"]').set_input_files(sorted((APLUSB / "tests").iterdir()))
    page.locator('input[name="samples"]').fill("01")
    page.locator('input[name="solution1"]').set_input_files(APLUSB / "solutions" / "main.cpp")
    page.locator('select[name="solution1_lang"]').select_option("cpp17")
    page.locator('input[name="solution1_score"]').fill("100")
    page.screenshot(path=str(SCREENSHOTS / "mu-editor-form.png"), full_page=True)
    page.locator(".submit-row input[type=submit]").click()

    # The version page reloads itself while the worker validates.
    expect(page).to_have_url(re.compile(rf"^{ADMIN_URL}/admin/grader/versions/\d+/$"))
    publish = page.locator('form[action$="/publish"] input[type=submit]')
    expect(publish).to_be_visible(timeout=JUDGE_TIMEOUT_MS)

    # Open it with the switch on the Problems page.
    page.goto(f"{ADMIN_URL}/admin/grader/problems/?q={slug}")
    row = page.locator(f'tr[data-slug="{slug}"]')
    expect(row.locator("td[data-open]")).to_have_attribute("data-open", "no")
    row.locator("button.switch").click()
    expect(row.locator("td[data-open]")).to_have_attribute("data-open", "yes")

    # A student solves it.
    student = page.context.new_page()
    student.goto(f"{PUBLIC_URL}/problems/{slug}")
    expect(student.locator("main")).to_contain_text("บวกเลข")
    sign_in_new_account(student)
    student.goto(f"{PUBLIC_URL}/problems/{slug}/submit")
    student.locator('select[name="language"]').select_option("cpp17")
    student.locator('textarea[name="source"]').fill((APLUSB / "solutions" / "main.cpp").read_text())
    student.locator("main form button[type=submit]").click()
    expect(student.locator("#results")).to_contain_text("ผ่าน", timeout=JUDGE_TIMEOUT_MS)
    expect(student.locator("#results")).to_contain_text("คะแนน: 100")
    student.close()
