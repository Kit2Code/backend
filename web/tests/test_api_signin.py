"""SPA sign-in (contract §6, PLAN.md §7.1): redirects, registration, logout, settings.

Token and ID-token checks are shared with the old pages and tested in depth by
test_google_signin.py; these tests cover what the API adds.
"""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from accounts.models import PRIVACY_VERSION, GoogleIdentity, User
from core.models import SiteSetting
from tests.conftest import EdgeClient
from tests.test_google_signin import (
    SUB,
    FakeGoogle,
    good_claims,
    google,  # noqa: F401 (fixture)
    make_member,
    signed_in_user,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def start(client: Client, next_url: str | None = None) -> dict[str, str]:
    response = client.get("/api/auth/google/start", {"next": next_url} if next_url else {})
    assert response.status_code == 302, response.content
    return {k: v[0] for k, v in parse_qs(urlsplit(response["Location"]).query).items()}


def sign_in(client: Client, google: FakeGoogle, next_url: str | None = None) -> Any:  # noqa: F811
    params = start(client, next_url)
    google.claims = good_claims(params)
    return client.get("/api/auth/google/callback", {"state": params["state"], "code": "c"})


def csrf(client: Client) -> str:
    return str(client.get("/api/auth/me").json()["csrf_token"])


def post(client: Client, url: str, data: Any = None, method: str = "post") -> Any:
    return getattr(client, method)(
        url, data or {}, "application/json", HTTP_X_CSRFTOKEN=csrf(client)
    )


def strict() -> Client:
    return EdgeClient(enforce_csrf_checks=True)


# ---- redirects ----


def test_start_uses_the_api_callback(google: FakeGoogle, settings: Any) -> None:  # noqa: F811
    assert start(EdgeClient())["redirect_uri"] == settings.GOOGLE_API_REDIRECT_URI
    assert settings.GOOGLE_API_REDIRECT_URI.endswith("/api/auth/google/callback")


def test_known_member_goes_to_next(google: FakeGoogle) -> None:  # noqa: F811
    make_member()
    client = EdgeClient()
    response = sign_in(client, google, "/problems/aplusb")
    assert (response.status_code, response["Location"]) == (302, "/problems/aplusb")
    assert signed_in_user(client) == "alice"
    assert google.token_requests[0]["form"]["redirect_uri"].endswith("/api/auth/google/callback")


@pytest.mark.parametrize(
    "next_url", ["https://evil.example/", "//evil.example", "/\\evil.example", "javascript:x"]
)
def test_open_redirects_go_home(google: FakeGoogle, next_url: str) -> None:  # noqa: F811
    make_member()
    assert sign_in(EdgeClient(), google, next_url)["Location"] == "/"


def test_new_account_goes_to_welcome(google: FakeGoogle) -> None:  # noqa: F811
    client = EdgeClient()
    assert sign_in(client, google)["Location"] == "/welcome"
    assert signed_in_user(client) is None


@pytest.mark.parametrize(
    ("setup", "code"),
    [("disabled", "account_disabled"), ("closed", "registration_closed"), ("bad", "failed")],
)
def test_failures_go_to_signin_error(google: FakeGoogle, setup: str, code: str) -> None:  # noqa: F811
    if setup == "disabled":
        user = make_member()
        User.objects.filter(pk=user.pk).update(is_active=False)
    if setup == "closed":
        SiteSetting.objects.update(registration_open=False)
    client = EdgeClient()
    if setup == "bad":
        start(client)
        response = client.get("/api/auth/google/callback", {"state": "wrong", "code": "c"})
    else:
        response = sign_in(client, google)
    assert response["Location"] == f"/signin-error?code={code}"
    assert signed_in_user(client) is None


def test_unavailable_when_not_configured() -> None:
    response = EdgeClient().get("/api/auth/google/start")
    assert response["Location"] == "/signin-error?code=unavailable"


# ---- registration ----


def registered(google: FakeGoogle, **fields: Any) -> tuple[Client, Any]:  # noqa: F811
    client = strict()
    sign_in(client, google)
    data = {"username": "alice", "display_name": "Alice A.", "accept_privacy": True, **fields}
    return client, post(client, "/api/auth/register", data)


def test_register(google: FakeGoogle) -> None:  # noqa: F811
    client, response = registered(google)
    assert response.status_code == 201, response.content
    assert response.json()["user"]["username"] == "alice"
    assert signed_in_user(client) == "alice"
    user = User.objects.get(username="alice")
    assert user.privacy_version == PRIVACY_VERSION
    assert user.privacy_accepted_at is not None
    assert GoogleIdentity.objects.get(user=user).subject == SUB
    assert client.get("/api/auth/me").json()["user"]["username"] == "alice"


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"username": "Al"}, "username"),
        ({"username": "has space"}, "username"),
        ({"display_name": ""}, "display_name"),
        ({"display_name": "a\x00b"}, "display_name"),
        ({"display_name": "x" * 101}, "display_name"),
        ({"accept_privacy": False}, "accept_privacy"),
    ],
)
def test_register_validation(google: FakeGoogle, fields: dict[str, Any], field: str) -> None:  # noqa: F811
    _client, response = registered(google, **fields)
    assert response.status_code == 400
    error = response.json()["error"]
    assert (error["code"], list(error["fields"])) == ("validation_error", [field])


def test_register_taken_username(google: FakeGoogle) -> None:  # noqa: F811
    User.objects.create_user(username="alice")
    _client, response = registered(google)
    assert response.json()["error"]["fields"].keys() == {"username"}


def test_register_without_pending_signin() -> None:
    client = strict()
    response = post(client, "/api/auth/register", {"username": "alice"})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "no_pending_signin")


def test_register_when_closed(google: FakeGoogle) -> None:  # noqa: F811
    client = strict()
    sign_in(client, google)
    SiteSetting.objects.update(registration_open=False)
    response = post(client, "/api/auth/register", {"username": "alice"})
    assert (response.status_code, response.json()["error"]["code"]) == (403, "registration_closed")


def test_register_needs_csrf(google: FakeGoogle) -> None:  # noqa: F811
    client = strict()
    sign_in(client, google)
    response = client.post("/api/auth/register", {}, "application/json")
    assert response.json()["error"]["code"] == "csrf_failed"


# ---- logout and settings ----


def signed_in() -> tuple[Client, User]:
    user = User.objects.create_user(username="alice", display_name="Alice")
    client = strict()
    client.force_login(user)
    return client, user


def test_logout() -> None:
    client, _user = signed_in()
    assert post(client, "/api/auth/logout").status_code == 204
    assert signed_in_user(client) is None


def test_logout_everywhere_ends_other_sessions() -> None:
    client, user = signed_in()
    other = EdgeClient()
    other.force_login(user)
    assert post(client, "/api/auth/logout-everywhere").status_code == 204
    assert other.get("/api/auth/me").json()["user"] is None


def test_patch_me() -> None:
    client, user = signed_in()
    response = post(client, "/api/me", {"display_name": "Kit K."}, method="patch")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Kit K."
    user.refresh_from_db()
    assert (user.display_name, user.show_photo) == ("Kit K.", True)  # unsent field unchanged
    post(client, "/api/me", {"show_photo": False}, method="patch")
    user.refresh_from_db()
    assert (user.display_name, user.show_photo) == ("Kit K.", False)


def test_patch_me_validation_and_auth() -> None:
    client, _user = signed_in()
    response = post(client, "/api/me", {"display_name": ""}, method="patch")
    assert response.json()["error"]["fields"].keys() == {"display_name"}
    anonymous = strict()
    response = post(anonymous, "/api/me", {"display_name": "x"}, method="patch")
    assert response.status_code == 401
