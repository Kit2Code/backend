"""The admin Sign-in page: the Google client ID and its write-only secret, the redirect URI to
register, and a button that checks Google's keys can be fetched (through the edge's :8090).
Also: the users list shows who is Google-linked, and staff can unlink a Google account.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from joserfc.jwk import RSAKey
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import GoogleIdentity, User
from core import crypto
from core.models import AuditLog, SiteSetting
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PAGE = "/admin/grader/signin/"
PASSWORD = "a-long-test-password-1"
SECRET = "GOCSPX-super-secret-value-123"


@pytest.fixture
def staff() -> Client:
    client = EdgeClient()
    client.force_login(User.objects.create_superuser("boss", password=PASSWORD))
    return client


def save(client: Client, client_id: str, secret: str = "") -> Any:
    return client.post(PAGE, {"action": "save", "client_id": client_id, "client_secret": secret})


@pytest.fixture
def keys_server() -> Iterator[dict[str, Any]]:
    """A local stand-in for :8090/certs; `answer` says what it replies."""
    keys = [RSAKey.generate_key(2048).as_dict(private=False) for _ in range(2)]
    answer: dict[str, Any] = {"status": 200, "body": json.dumps({"keys": keys})}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            data = answer["body"].encode()
            self.send_response(answer["status"])
            if "location" in answer:
                self.send_header("Location", answer["location"])
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    answer["url"] = f"http://127.0.0.1:{server.server_address[1]}/certs"
    yield answer
    server.shutdown()
    server.server_close()


# ---- access ----


def test_the_page_is_for_staff_who_may_change_settings() -> None:
    assert EdgeClient().get(PAGE).status_code == 302
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(PAGE).status_code == 403
    assert save(client, "id.apps.googleusercontent.com", SECRET).status_code == 403
    helper.user_permissions.add(Permission.objects.get(codename="change_sitesetting"))
    assert client.get(PAGE).status_code == 200


# ---- the client ID and secret ----


def test_the_redirect_uri_to_register_is_shown(staff: Client) -> None:
    html = staff.get(PAGE).content.decode()
    assert "http://127.0.0.1:8000/auth/google/callback" in html


def test_saving_stores_the_secret_encrypted_and_never_shows_it(staff: Client) -> None:
    response = save(staff, "123.apps.googleusercontent.com", SECRET)
    assert response.status_code == 302
    setting = SiteSetting.get()
    assert setting.google_client_id == "123.apps.googleusercontent.com"
    assert setting.google_client_secret_encrypted != SECRET
    assert crypto.decrypt_secret(setting.google_client_secret_encrypted) == SECRET
    html = staff.get(PAGE).content.decode()
    assert "123.apps.googleusercontent.com" in html
    assert SECRET not in html
    assert setting.google_client_secret_encrypted not in html
    assert 'data-secret="set"' in html


def test_the_secret_is_not_echoed_when_the_form_has_an_error(staff: Client) -> None:
    response = save(staff, "", SECRET)  # the client ID is required
    assert response.status_code == 200
    assert SECRET not in response.content.decode()
    assert not SiteSetting.get().google_client_secret_encrypted


def test_a_blank_secret_keeps_the_old_one(staff: Client) -> None:
    save(staff, "first.apps.googleusercontent.com", SECRET)
    save(staff, "second.apps.googleusercontent.com", "")
    setting = SiteSetting.get()
    assert setting.google_client_id == "second.apps.googleusercontent.com"
    assert crypto.decrypt_secret(setting.google_client_secret_encrypted) == SECRET


def test_no_secret_yet_is_shown_as_not_set(staff: Client) -> None:
    assert 'data-secret="unset"' in staff.get(PAGE).content.decode()


def test_saving_is_audited_without_the_secret(staff: Client) -> None:
    save(staff, "123.apps.googleusercontent.com", SECRET)
    row = AuditLog.objects.get(action="signin.save")
    assert row.details == {"client_id": "123.apps.googleusercontent.com", "secret_changed": True}
    everything = json.dumps([list(r) for r in AuditLog.objects.values_list()], default=str)
    assert SECRET not in everything


def test_control_characters_in_the_client_id_are_refused(staff: Client) -> None:
    assert save(staff, "bad‮id", SECRET).status_code == 200
    assert not SiteSetting.get().google_client_id


# ---- the test button ----


def test_the_test_button_counts_the_keys(
    staff: Client, settings: SettingsWrapper, keys_server: dict[str, Any]
) -> None:
    settings.GOOGLE_JWKS_URL = keys_server["url"]
    html = staff.post(PAGE, {"action": "test"}).content.decode()
    assert 'data-test="ok"' in html
    assert 'data-keys="2"' in html
    assert AuditLog.objects.filter(action="signin.test").exists()


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 500, "body": "oops"},
        {"status": 200, "body": "not json"},
        {"status": 200, "body": json.dumps({"keys": []})},
        {"status": 302, "body": "", "location": "http://127.0.0.1:9/elsewhere"},
    ],
)
def test_the_test_button_reports_failures(
    staff: Client, settings: SettingsWrapper, keys_server: dict[str, Any], answer: dict[str, Any]
) -> None:
    keys_server.update(answer)
    settings.GOOGLE_JWKS_URL = keys_server["url"]
    html = staff.post(PAGE, {"action": "test"}).content.decode()
    assert 'data-test="fail"' in html


def test_the_test_button_reports_an_unreachable_edge(
    staff: Client, settings: SettingsWrapper
) -> None:
    settings.GOOGLE_JWKS_URL = "http://127.0.0.1:9/certs"
    assert 'data-test="fail"' in staff.post(PAGE, {"action": "test"}).content.decode()


# ---- users ----


def test_unlink_google_removes_the_link_and_is_audited(staff: Client) -> None:
    alice = User.objects.create_user("alice")
    GoogleIdentity.objects.create(user=alice, issuer="https://accounts.google.com", subject="123")
    listing = staff.get("/admin/accounts/user/").content.decode()
    assert "icon-yes.svg" in listing  # alice is linked
    data = {"action": "unlink_google", "_selected_action": [str(alice.pk)]}
    assert staff.post("/admin/accounts/user/", data).status_code == 302
    assert not GoogleIdentity.objects.exists()
    row = AuditLog.objects.get(action="user.unlink_google")
    assert row.target == "alice"
    assert row.details == {"issuer": "https://accounts.google.com"}
