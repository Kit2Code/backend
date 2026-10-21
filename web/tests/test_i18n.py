"""Thai (the default) and English: translations, the language switch, the base template."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from tests.conftest import EdgeClient

WEB = Path(__file__).resolve().parents[1]
LOCALE = WEB / "locale"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def page_language(client: Client, path: str = "/") -> str:
    html = client.get(path).content.decode()
    match = re.search(r'<html lang="([a-z-]+)"', html)
    assert match, html[:200]
    return match.group(1)


def test_thai_is_the_default() -> None:
    client = EdgeClient()
    assert page_language(client) == "th"
    assert "โจทย์" in client.get("/").content.decode()


def test_browser_language_does_not_override_thai() -> None:
    # Many students' browsers say English; the site stays Thai until they choose English.
    client = EdgeClient(headers={"Accept-Language": "en-US,en;q=0.9"})
    assert page_language(client) == "th"


def test_switch_to_english_and_back() -> None:
    client = EdgeClient()
    response = client.post("/i18n/setlang/", {"language": "en", "next": "/privacy"})
    assert response.status_code == 302
    assert response["Location"] == "/privacy"
    assert page_language(client, "/privacy") == "en"
    assert "Privacy" in client.get("/privacy").content.decode()
    client.post("/i18n/setlang/", {"language": "th", "next": "/"})
    assert page_language(client) == "th"


def test_language_switch_needs_post_with_csrf() -> None:
    client = EdgeClient(enforce_csrf_checks=True)
    assert client.post("/i18n/setlang/", {"language": "en"}).status_code == 403
    assert page_language(client) == "th"


def test_language_switch_ignores_an_outside_next() -> None:
    client = EdgeClient()
    response = client.post("/i18n/setlang/", {"language": "en", "next": "https://evil.example/"})
    assert response["Location"] == "/"


def test_unknown_language_is_ignored() -> None:
    client = EdgeClient()
    client.post("/i18n/setlang/", {"language": "fr", "next": "/"})
    assert page_language(client) == "th"


def test_only_thai_and_english() -> None:
    assert [code for code, _name in settings.LANGUAGES] == ["th", "en"]
    assert settings.LANGUAGE_CODE == "th"


def test_site_name_follows_the_language(settings: SettingsWrapper) -> None:
    settings.SITE_NAME_TH = "เกรดเดอร์ทดสอบ"
    settings.SITE_NAME_EN = "Test Grader"
    client = EdgeClient()
    assert "เกรดเดอร์ทดสอบ" in client.get("/").content.decode()
    client.post("/i18n/setlang/", {"language": "en", "next": "/"})
    assert "Test Grader" in client.get("/").content.decode()


def test_base_template_uses_the_vendored_css_and_font() -> None:
    html = EdgeClient().get("/").content.decode()
    assert "/static/vendor/pico/pico.classless.min.css" in html
    assert "/static/site.css" in html
    assert "https://" not in html.split("<body")[0]  # nothing loaded from other sites
    site_css = Path(str(finders.find("site.css"))).read_text(encoding="utf-8")
    assert "ibm-plex-sans-thai-thai-400-normal.woff2" in site_css
    for name in [
        "vendor/pico/pico.classless.min.css",
        "vendor/fonts/ibm-plex-sans-thai-thai-400-normal.woff2",
        "vendor/fonts/ibm-plex-sans-thai-latin-400-normal.woff2",
        "vendor/fonts/OFL.txt",
    ]:
        assert finders.find(name), name


def test_static_files_are_served() -> None:
    response = EdgeClient().get("/static/site.css")
    assert response.status_code == 200


# ---- the translation files ----


def msgfmt_statistics(po: Path) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv list
        ["msgfmt", "--check", "--statistics", "-o", "/dev/null", str(po)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stderr


@pytest.mark.parametrize("language", ["th", "en"])
def test_po_files_are_valid_and_complete(language: str) -> None:
    po = LOCALE / language / "LC_MESSAGES" / "django.po"
    stats = msgfmt_statistics(po)
    assert "translated message" in stats
    assert "untranslated" not in stats and "fuzzy" not in stats, stats


def test_every_string_in_the_code_is_translated(tmp_path: Path) -> None:
    """Run makemessages on a copy of the code: it must find no string missing from the .po file."""
    copy = tmp_path / "web"
    shutil.copytree(
        WEB, copy, ignore=shutil.ignore_patterns("__pycache__", "staticfiles", "tests", "*.mo")
    )
    result = subprocess.run(  # noqa: S603 - fixed argv list
        [
            sys.executable,
            "manage.py",
            "makemessages",
            "--locale",
            "th",
            "--no-obsolete",
            "--ignore",
            "static/*",
        ],
        cwd=copy,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    stats = msgfmt_statistics(copy / "locale" / "th" / "LC_MESSAGES" / "django.po")
    assert "untranslated" not in stats and "fuzzy" not in stats, stats


def test_compilemessages_succeeds() -> None:
    call_command("compilemessages", locale=["th", "en"], verbosity=0)
    assert (LOCALE / "th" / "LC_MESSAGES" / "django.mo").is_file()
