"""First-run setup: a one-time link that creates the first superuser, then stops working."""

import hashlib
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core.models import AuditLog, SetupToken
from tests.conftest import EdgeClient, use_role

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PASSWORD = "a-long-test-password-1"


@pytest.fixture(autouse=True)
def uploads(settings: SettingsWrapper, tmp_path: Path) -> Path:
    settings.GRADER_UPLOADS_DIR = tmp_path
    return tmp_path


def issue_link() -> str:
    """Run the start-up command and return the link it printed."""
    out = StringIO()
    call_command("setup_link", stdout=out)
    match = re.search(r"https?://\S+/setup\?token=(\S+)", out.getvalue())
    assert match, out.getvalue()
    return match.group(0)


def token_of(link: str) -> str:
    return link.split("token=", 1)[1]


def open_link(client: Client, link: str) -> int:
    """Follow the link like a browser: /setup?token=… redirects to /setup."""
    response = client.get("/setup", {"token": token_of(link)})
    if response.status_code != 302:
        return response.status_code
    assert response["Location"] == "/setup"
    assert "token" not in response["Location"]
    return client.get("/setup").status_code


def send_form(client: Client, username: str = "boss") -> int:
    data = {"username": username, "password1": PASSWORD, "password2": PASSWORD}
    return client.post("/setup", data).status_code


# ---- the link ----


def test_the_link_is_printed_and_written_to_the_uploads_folder(uploads: Path) -> None:
    link = issue_link()
    assert link.startswith("http://127.0.0.1:8001/setup?token=")
    assert (uploads / "SETUP-LINK.txt").read_text(encoding="utf-8").strip() == link


def test_only_the_hash_of_the_token_is_stored() -> None:
    token = token_of(issue_link())
    row = SetupToken.objects.get()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    assert len(token) >= 40


def test_the_link_expires_after_24_hours() -> None:
    issue_link()
    row = SetupToken.objects.get()
    lifetime = row.expires_at - timezone.now()
    assert timedelta(hours=23, minutes=59) < lifetime <= timedelta(hours=24)


def test_each_start_makes_a_new_link_and_the_old_one_stops_working() -> None:
    old = issue_link()
    new = issue_link()
    assert old != new
    assert open_link(EdgeClient(), old) == 404
    assert open_link(EdgeClient(), new) == 200


def test_no_link_once_a_superuser_exists(uploads: Path) -> None:
    issue_link()
    User.objects.create_superuser("root", password=PASSWORD)
    out = StringIO()
    call_command("setup_link", stdout=out)
    assert "token=" not in out.getvalue()
    assert not (uploads / "SETUP-LINK.txt").exists()
    assert not SetupToken.objects.filter(used_at__isnull=True).exists()


# ---- using it ----


def test_the_link_creates_the_first_superuser_once(uploads: Path) -> None:
    link = issue_link()
    client = EdgeClient()
    assert open_link(client, link) == 200
    assert send_form(client) == 302
    user = User.objects.get(username="boss")
    assert user.is_superuser and user.is_staff and user.check_password(PASSWORD)
    assert not (uploads / "SETUP-LINK.txt").exists()
    assert SetupToken.objects.get().used_at is not None
    # Used: the same link, in a new browser or the same one, does nothing any more.
    assert open_link(EdgeClient(), link) == 404
    assert client.get("/setup").status_code == 404
    assert send_form(client, "second") == 404
    assert User.objects.filter(is_superuser=True).count() == 1


def test_setup_is_audited() -> None:
    client = EdgeClient()
    open_link(client, issue_link())
    send_form(client)
    row = AuditLog.objects.get(action="setup.superuser_created")
    assert row.actor_username == "boss"
    assert row.ip == "127.0.0.1"


def test_a_wrong_token_is_404() -> None:
    issue_link()
    assert open_link(EdgeClient(), "http://x/setup?token=not-the-token") == 404


def test_without_a_token_the_page_is_404() -> None:
    issue_link()
    client = EdgeClient()
    assert client.get("/setup").status_code == 404
    assert send_form(client) == 404


def test_an_expired_link_is_404() -> None:
    link = issue_link()
    SetupToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert open_link(EdgeClient(), link) == 404


def test_an_expired_link_is_refused_even_after_opening_it() -> None:
    link = issue_link()
    client = EdgeClient()
    assert open_link(client, link) == 200
    SetupToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert send_form(client) == 404
    assert not User.objects.exists()


def test_the_link_stops_working_if_a_superuser_appears_meanwhile() -> None:
    link = issue_link()
    User.objects.create_superuser("root", password=PASSWORD)
    assert open_link(EdgeClient(), link) == 404


def test_weak_or_mismatched_passwords_are_refused() -> None:
    client = EdgeClient()
    open_link(client, issue_link())
    weak = {"username": "boss", "password1": "short", "password2": "short"}
    assert client.post("/setup", weak).status_code == 200
    mismatch = {"username": "boss", "password1": PASSWORD, "password2": PASSWORD + "x"}
    assert client.post("/setup", mismatch).status_code == 200
    assert not User.objects.exists()


def test_tries_are_rate_limited() -> None:
    client = EdgeClient()
    open_link(client, issue_link())
    bad = {"username": "boss", "password1": "short", "password2": "short"}
    codes = [client.post("/setup", bad).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_the_public_role_has_no_setup_page(settings: SettingsWrapper) -> None:
    link = issue_link()
    use_role(settings, "public")
    assert EdgeClient().get("/setup", {"token": token_of(link)}).status_code == 404


def test_the_session_holds_only_the_hash_of_the_token() -> None:
    link = issue_link()
    client = EdgeClient()
    open_link(client, link)
    stored = str(dict(client.session.items()))
    assert token_of(link) not in stored
    assert hashlib.sha256(token_of(link).encode()).hexdigest() in stored
