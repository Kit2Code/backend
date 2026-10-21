"""A visitor signs in with (fake) Google, picks a username, opens a problem and its PDF, and
switches to English."""

import re
import secrets

from playwright.sync_api import Page, expect

from tests.e2e.conftest import PUBLIC_URL, REPO, SCREENSHOTS, import_into_dev_stack


def test_sign_in_browse_and_switch_language(page: Page) -> None:
    slug = import_into_dev_stack(REPO / "fixtures" / "cms-italian" / "groupmin")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)

    # The site starts in Thai.
    page.goto(f"{PUBLIC_URL}/")
    expect(page.locator("html")).to_have_attribute("lang", "th")

    # Sign in with the fake Google: pick a brand-new account.
    page.get_by_role("link", name="เข้าสู่ระบบด้วย Google").click()
    expect(page).to_have_url(re.compile(r"^http://127\.0\.0\.1:8099/authorize\?"))
    page.get_by_label("Other account id").fill("e2e-" + secrets.token_hex(8))
    page.get_by_role("button", name="Use it").click()

    # First sign-in: choose a username.
    expect(page).to_have_url(f"{PUBLIC_URL}/auth/welcome")
    username = "e2e_" + secrets.token_hex(4)
    page.get_by_label("ชื่อผู้ใช้").fill(username)
    page.get_by_label("ชื่อที่แสดง").fill("ทดสอบ อีทูอี")
    page.get_by_label("ฉันอ่านนโยบายความเป็นส่วนตัวแล้ว").check()
    page.screenshot(path=SCREENSHOTS / "welcome-th.png", full_page=True)
    page.get_by_role("button", name="สร้างบัญชีของฉัน").click()

    # Signed in, back where we started.
    expect(page).to_have_url(f"{PUBLIC_URL}/")
    expect(page.locator("header")).to_contain_text(username)

    # The problem list, then the problem, in Thai.
    page.get_by_role("link", name="โจทย์", exact=True).click()
    expect(page).to_have_url(f"{PUBLIC_URL}/problems")
    page.locator(f'a[href="/problems/{slug}"]').click()
    expect(page.locator("main")).to_contain_text("เวลาที่กำหนด")
    page.screenshot(path=SCREENSHOTS / "problem-th.png", full_page=True)

    # The statement PDF.
    pdf_link = page.locator(f'a[href="/problems/{slug}/files/statement/th.pdf"]')
    expect(pdf_link).to_be_visible()
    response = page.request.get(f"{PUBLIC_URL}/problems/{slug}/files/statement/th.pdf")
    assert response.status == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.body().startswith(b"%PDF")

    # Switch to English: same page, English UI.
    page.get_by_role("button", name="English").click()
    expect(page).to_have_url(f"{PUBLIC_URL}/problems/{slug}")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("main")).to_contain_text("Time limit")
    expect(page.locator("main")).to_contain_text("C11, C++17, C++20, Python 3")
    page.screenshot(path=SCREENSHOTS / "problem-en.png", full_page=True)
