"""The admin site end to end, in a real browser against the dev stack (real worker and isolate).

First-run setup link → staff sign-in → Sign-in page (save + test) → import a CMS Italian zip →
validation by the worker → publish → visible publicly; a zip whose reference solution fails stays
hidden; pause → a submission waits → resume → judged; calibration; self-test; "back up now"; and
an audit row for each action, including a plain Django admin edit.

Each run imports problems with fresh slugs and content, so nothing is reused from an earlier run.
"""

import json
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import PUBLIC_URL, REPO, SCREENSHOTS, sign_in_new_account

ADMIN_URL = "http://127.0.0.1:8001"
GROUPMIN = REPO / "fixtures" / "cms-italian" / "groupmin"
SETUP_LINK = REPO / ".devdata" / "uploads" / "SETUP-LINK.txt"
# The dev stack's fake Google client (init.sh sets the same ID; its secret stays as it is).
DEV_CLIENT_ID = "grader-dev.apps.example"
JUDGE_TIMEOUT_MS = 180_000


def manage(*argv: str) -> str:
    """Run the developer machine's manage.py against the dev stack's database."""
    result = subprocess.run(  # noqa: S603 - fixed argv list
        [sys.executable, "manage.py", *argv],
        cwd=REPO / "web",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def shell(code: str) -> str:
    return manage("shell", "-c", code)


def http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - the dev stack
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


def problem_zip(tmp_path: Path, slug: str, *, failing: bool = False) -> Path:
    """The CMS Italian groupmin task as a zip, renamed to `slug`, with a title unique to this run.

    `failing`: soluzione.cpp (which must get full marks) is replaced by the partial solution.
    """
    target = tmp_path / f"{slug}.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for path in sorted(GROUPMIN.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(GROUPMIN).as_posix()
            data = path.read_bytes()
            if name == "task.yaml":
                text = data.decode().replace("name: groupmin", f"name: {slug}")
                data = text.replace("title: Sum of numbers", f"title: E2E {slug}").encode()
            if failing and name == "sol/soluzione.cpp":
                data = (GROUPMIN / "sol" / "partial.cpp").read_bytes()
            archive.writestr(name, data)
    return target


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(SCREENSHOTS / f"m5-{name}.png"), full_page=True)


@pytest.fixture(scope="module")
def admin_login() -> Iterator[dict[str, str]]:
    """A fresh first-run state: no superuser, a new setup link. Grading is resumed afterwards."""
    shell(
        "from accounts.models import User; from core.models import BackupRun\n"
        "User.objects.filter(is_superuser=True).delete()\n"
        "BackupRun.objects.filter(status__in=['requested', 'running'])"
        ".update(status='failed', message='left over from an earlier test run')"
    )
    manage("setup_link")
    login = {"username": "e2e_boss_" + secrets.token_hex(3), "password": secrets.token_urlsafe(16)}
    login["link"] = SETUP_LINK.read_text(encoding="utf-8").strip()
    yield login
    shell(
        "from core.models import SiteSetting; s = SiteSetting.get(); s.grading_paused = False\n"
        "s.save()"
    )


def sign_in_as_staff(page: Page, login: dict[str, str]) -> None:
    page.goto(f"{ADMIN_URL}/admin/login/")
    page.locator("#id_username").fill(login["username"])
    page.locator("#id_password").fill(login["password"])
    page.locator("input[type=submit]").click()
    expect(page.locator("#grader-pages")).to_be_visible()


def post_action(page: Page, action: str) -> None:
    """Click the submit button of the form whose hidden `action` field is `action`."""
    form = page.locator(f'form:has(input[name="action"][value="{action}"])')
    form.locator("input[type=submit]").click()


def upload(page: Page, zip_path: Path) -> None:
    page.goto(f"{ADMIN_URL}/admin/grader/import/")
    page.locator('input[name="file"]').set_input_files(str(zip_path))
    page.locator(".submit-row input[type=submit]").click()
    expect(page).to_have_url(re.compile(rf"^{ADMIN_URL}/admin/grader/versions/\d+/$"))


def audit_actions() -> list[str]:
    out = shell(
        "import json; from core.models import AuditLog\n"
        "print(json.dumps(list(AuditLog.objects.values_list('action', flat=True))))"
    )
    return list(json.loads(out.strip().splitlines()[-1]))


# ---- the whole flow ----


def test_the_admin_flow(page: Page, admin_login: dict[str, str], tmp_path: Path) -> None:
    # First-run setup: the link creates the first superuser, then stops working.
    page.goto(admin_login["link"])
    expect(page).to_have_url(f"{ADMIN_URL}/setup")
    page.locator('input[name="username"]').fill(admin_login["username"])
    page.locator('input[name="password1"]').fill(admin_login["password"])
    page.locator('input[name="password2"]').fill(admin_login["password"])
    shot(page, "setup")
    page.locator(".submit-row input[type=submit]").click()
    expect(page).to_have_url(re.compile(rf"^{ADMIN_URL}/admin/login/"))
    assert not SETUP_LINK.exists()
    assert http_status(admin_login["link"]) == 404
    sign_in_as_staff(page, admin_login)
    shot(page, "index")

    # Sign-in page: save the dev client ID (the secret is kept), and the test reaches the keys.
    page.goto(f"{ADMIN_URL}/admin/grader/signin/")
    page.locator('input[name="client_id"]').fill(DEV_CLIENT_ID)
    post_action(page, "save")
    expect(page.locator('[data-secret="set"]')).to_be_visible()
    post_action(page, "test")
    expect(page.locator('[data-test="ok"]')).to_be_visible()
    shot(page, "signin")

    # Import → validation by the real worker → publish → public.
    slug = "e2e-" + secrets.token_hex(3)
    upload(page, problem_zip(tmp_path, slug))
    version_url = page.url
    assert http_status(f"{PUBLIC_URL}/problems/{slug}") == 404
    publish = page.locator('form[action$="/publish"] input[type=submit]')
    expect(publish).to_be_visible(timeout=JUDGE_TIMEOUT_MS)  # the page reloads while validating
    shot(page, "validated")
    publish.click()
    expect(page).to_have_url(version_url)
    assert http_status(f"{PUBLIC_URL}/problems/{slug}") == 200

    # A plain Django admin edit is audited too.
    page.goto(f"{ADMIN_URL}/admin/problems/problem/?q={slug}")
    page.get_by_role("link", name=slug, exact=True).click()
    page.locator('input[name="title_en"]').fill(f"E2E {slug} (edited)")
    page.locator('input[name="_save"]').click()
    expect(page.locator(".messagelist .success")).to_be_visible()

    # Pause: a new submission waits; resume: it is judged.
    page.goto(f"{ADMIN_URL}/admin/grader/grading/")
    post_action(page, "pause")
    expect(page.locator('[data-state="paused"]')).to_be_visible()
    student = page.context.new_page()
    student.goto(f"{PUBLIC_URL}/problems/{slug}")
    sign_in_new_account(student)
    student.goto(f"{PUBLIC_URL}/problems/{slug}/submit")
    student.locator('select[name="language"]').select_option("cpp17")
    student.locator('textarea[name="source"]').fill(
        (GROUPMIN / "sol" / "soluzione.cpp").read_text()
    )
    student.locator("main form button[type=submit]").click()
    expect(student).to_have_url(re.compile(rf"^{PUBLIC_URL}/submissions/\d+$"))
    student.wait_for_timeout(6_000)  # three claim rounds of the worker
    expect(student.locator("#results")).to_contain_text("รอตรวจ")
    post_action(page, "resume")
    expect(page.locator('[data-state="running"]')).to_be_visible()
    expect(student.locator("#results")).to_contain_text("ผ่าน", timeout=JUDGE_TIMEOUT_MS)
    student.close()

    # Calibration times the reference solutions and suggests a scale.
    page.goto(f"{ADMIN_URL}/admin/grader/calibrate/")
    page.locator(f'[data-problem="{slug}"] input[type=submit]').click()
    expect(page.locator(f'[data-suggested="{slug}"]')).to_be_visible(timeout=JUDGE_TIMEOUT_MS)
    shot(page, "calibrate")

    # The self-test: every hostile program is contained on this host.
    page.goto(f"{ADMIN_URL}/admin/grader/system/")
    post_action(page, "selftest")
    expect(page.locator('[data-selftest="pass"]')).to_be_visible(timeout=JUDGE_TIMEOUT_MS)
    shot(page, "system")

    # Back up now: a request row for the backup service.
    page.goto(f"{ADMIN_URL}/admin/grader/backups/")
    post_action(page, "backup_now")
    expect(page.locator('[data-status="requested"]')).to_have_count(1)
    shot(page, "backups")

    actions = audit_actions()
    for action in (
        "setup.superuser_created", "auth.login", "signin.save", "signin.test", "problem.import",
        "problem.publish", "admin.change", "grading.pause", "grading.resume", "problem.calibrate",
        "system.selftest", "backup.request",
    ):  # fmt: skip
        assert action in actions, action


def test_a_failing_reference_solution_keeps_the_problem_hidden(
    page: Page, admin_login: dict[str, str], tmp_path: Path
) -> None:
    sign_in_as_staff(page, admin_login)
    slug = "e2e-bad-" + secrets.token_hex(3)
    upload(page, problem_zip(tmp_path, slug, failing=True))
    # Validation ends with a ✘ for soluzione.cpp and no Publish button.
    expect(page.locator("#solutions")).to_contain_text("✘", timeout=JUDGE_TIMEOUT_MS)
    expect(page.locator("#solutions")).to_contain_text("sol")
    expect(page.locator("#solutions")).to_contain_text("expected 100")
    expect(page.locator('form[action$="/publish"]')).to_have_count(0)
    shot(page, "invalid")
    assert http_status(f"{PUBLIC_URL}/problems/{slug}") == 404
