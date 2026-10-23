"""The look shared by every public page: dark theme, header, avatar menu, icons."""

import re
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.template import Context, Template, TemplateSyntaxError

from accounts.models import User
from core.models import SiteSetting
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def header_of(html: str) -> str:
    match = re.search(r"<header.*?</header>", html, re.S)
    assert match, html[:300]
    return match.group(0)


@pytest.fixture
def signin_set_up() -> None:
    setting = SiteSetting.get()
    setting.google_client_id = "client-id"
    setting.google_client_secret_encrypted = "not-empty"
    setting.save()


@pytest.fixture
def alice_client() -> EdgeClient:
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="alice"))
    return client


def test_pages_use_the_dark_theme() -> None:
    html = EdgeClient().get("/").content.decode()
    assert re.search(r'<html lang="th" data-theme="dark">', html)
    site_css = Path(str(finders.find("site.css"))).read_text(encoding="utf-8")
    assert "#0f172a" in site_css  # the navy background


def test_header_links_home_tasks_and_ranking() -> None:
    header = header_of(EdgeClient().get("/").content.decode())
    for href, label in [("/", "หน้าแรก"), ("/problems", "โจทย์"), ("/ranking", "อันดับ")]:
        assert re.search(rf'<a href="{href}"[^>]*>.*?{label}', header, re.S), label


def test_the_current_page_is_marked_in_the_header() -> None:
    header = header_of(EdgeClient().get("/problems").content.decode())
    assert re.search(r'<a href="/problems" aria-current="page"', header)
    assert not re.search(r'<a href="/ranking" aria-current="page"', header)


def test_signed_out_header_offers_google_sign_in(signin_set_up: None) -> None:
    header = header_of(EdgeClient().get("/").content.decode())
    assert "เข้าสู่ระบบด้วย Google" in header
    assert 'class="avatar-menu"' not in header


def test_signed_in_header_has_the_avatar_menu(alice_client: EdgeClient) -> None:
    header = header_of(alice_client.get("/").content.decode())
    menu = re.search(r'<details class="avatar-menu".*?</details>', header, re.S)
    assert menu, header
    assert "alice" in menu.group(0)
    assert 'href="/users/alice"' in menu.group(0)
    assert 'href="/submissions/mine"' in menu.group(0)
    assert re.search(r'<form method="post" action="/auth/logout"', menu.group(0))
    assert "csrfmiddlewaretoken" in menu.group(0)


def test_the_phone_menu_needs_no_javascript() -> None:
    html = EdgeClient().get("/").content.decode()
    header = header_of(html)
    assert re.search(r'<details class="nav-menu"', header)
    assert "<script" not in header
    assert " style=" not in html  # the CSP refuses inline styles


def test_language_switch_stays_in_the_header() -> None:
    header = header_of(EdgeClient().get("/").content.decode())
    assert 'action="/i18n/setlang/"' in header
    assert 'value="en"' in header


# ---- icons ----


def render(source: str) -> str:
    return Template("{% load icons %}" + source).render(Context())


def test_icon_tag_inlines_the_vendored_svg() -> None:
    svg = render('{% icon "folder" %}')
    assert svg.startswith("<svg")
    assert 'aria-hidden="true"' in svg
    assert 'class="icon"' in svg
    assert 'stroke="currentColor"' in svg
    assert "@license" not in svg


def test_unknown_icon_is_an_error() -> None:
    with pytest.raises(TemplateSyntaxError):
        render('{% icon "../../settings" %}')
    with pytest.raises(TemplateSyntaxError):
        render('{% icon "no-such-icon" %}')


def test_vendored_icons_keep_their_licence() -> None:
    assert finders.find("vendor/lucide/LICENSE")
    assert finders.find("vendor/lucide/folder.svg")
