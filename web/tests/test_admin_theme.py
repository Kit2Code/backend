"""The admin site wears the same dark navy theme and font as the public site."""

import re
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders

from accounts.models import User
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]


@pytest.fixture
def staff() -> EdgeClient:
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True, is_superuser=True))
    return client


def test_login_page_loads_the_theme() -> None:
    html = EdgeClient().get("/admin/login/").content.decode()
    assert "/static/admin-theme.css" in html


def test_admin_pages_load_the_theme_and_are_dark(staff: EdgeClient) -> None:
    for path in ["/admin/", "/admin/problems/problem/", "/admin/grader/system/"]:
        html = staff.get(path).content.decode()
        assert "/static/admin-theme.css" in html, path
        # Django's light/dark switching (its script and dark CSS) is replaced by our one theme.
        assert "admin/js/theme.js" not in html, path
        assert "admin/css/dark_mode.css" not in html, path


def test_no_light_dark_switch(staff: EdgeClient) -> None:
    html = staff.get("/admin/").content.decode()
    assert "theme-toggle" not in html


def test_theme_sets_django_admins_colours_and_our_font() -> None:
    css = Path(str(finders.find("admin-theme.css"))).read_text(encoding="utf-8")
    for variable in ["--primary", "--body-bg", "--darkened-bg", "--body-fg", "--header-bg"]:
        assert re.search(rf"{variable}:\s*#", css), variable
    assert "#0f172a" in css
    assert "IBM Plex Sans Thai" in css
    assert "https://" not in css  # nothing from other sites


def test_admin_pages_use_no_inline_styles(staff: EdgeClient) -> None:
    html = staff.get("/admin/").content.decode()
    assert "<style" not in html
