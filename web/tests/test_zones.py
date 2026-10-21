"""Zones and role-bound sessions: the second layer behind the edge.

The edge sets `X-Edge-Zone` (public on :8080, admin on :8081) and strips any copy sent by a client.
Django checks it again, so a request that skipped the edge, or came in on the wrong port, finds
nothing (404). `/internal/*` is the opposite: only the worker calls it, directly, never through the
edge, so it must have no zone header and must carry the worker token.
"""

import pytest
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from tests.conftest import use_role

ADMIN_PATH_VARIANTS = [
    "/admin/",
    "/admin",
    "/admin/login/",
    "/ADMIN/",
    "//admin/",
    "/%61dmin/",
    "/admin/../admin/",
    "/admin./",
    "/admin%2f",
    "/./admin/",
    "/internal/judge/claim",
    "/internal/worker/hello",
    "/setup",
    "/setup?token=x",
    "/setup/",
]


@pytest.mark.usefixtures("public_role")
@pytest.mark.parametrize("path", ADMIN_PATH_VARIANTS)
@pytest.mark.parametrize("zone", ["public", "admin", None])
def test_public_role_has_no_admin_internal_or_setup(path: str, zone: str | None) -> None:
    # Without a zone, also send the worker token: the judge API still must not exist here.
    if zone is None:
        headers = {"Authorization": "Bearer " + settings.WORKER_TOKEN}
    else:
        headers = {"X-Edge-Zone": zone}
    # The test client reads "//admin/" as host "admin" + path "/"; a real server passes the
    # path through as it is. Set PATH_INFO so the request is what a real server would give us.
    path_only = path.split("?")[0]
    response = Client(headers=headers).get(path, PATH_INFO=path_only)
    assert response.status_code == 404


@pytest.mark.usefixtures("public_role")
@pytest.mark.django_db
def test_public_role_has_no_password_login() -> None:
    client = Client(headers={"X-Edge-Zone": "public"})
    for path in ["/accounts/login/", "/login", "/login/", "/admin/login/", "/auth/login"]:
        assert client.get(path).status_code == 404
        assert client.post(path, {"username": "a", "password": "b"}).status_code == 404


@pytest.mark.usefixtures("admin_role")
@pytest.mark.django_db
def test_admin_role_needs_the_admin_zone() -> None:
    assert Client(headers={"X-Edge-Zone": "admin"}).get("/admin/").status_code == 302
    assert Client(headers={"X-Edge-Zone": "public"}).get("/admin/").status_code == 404
    assert Client(headers={"X-Edge-Zone": "Admin"}).get("/admin/").status_code == 404
    assert Client(headers={"X-Edge-Zone": "admin, public"}).get("/admin/").status_code == 404
    assert Client().get("/admin/").status_code == 404


WHOAMI = "tests.urls_whoami"


@pytest.mark.django_db
def test_public_role_needs_the_public_zone(settings: SettingsWrapper) -> None:
    use_role(settings, "public", WHOAMI)
    assert Client(headers={"X-Edge-Zone": "public"}).get("/whoami").status_code == 200
    assert Client(headers={"X-Edge-Zone": "admin"}).get("/whoami").status_code == 404
    assert Client(headers={"X-Edge-Zone": ""}).get("/whoami").status_code == 404
    assert Client().get("/whoami").status_code == 404


@pytest.mark.parametrize("role", ["public", "admin"])
def test_healthz_needs_no_zone(settings: SettingsWrapper, role: str) -> None:
    # Docker's healthcheck calls it from inside the container, not through the edge.
    use_role(settings, role)
    assert Client().get("/healthz").status_code == 200


# ---- /internal/* (admin role only) ----


class TestInternal:
    @pytest.fixture(autouse=True)
    def _admin_role_with_ping(self, settings: SettingsWrapper) -> None:
        use_role(settings, "admin", WHOAMI)

    def test_valid_token_and_no_zone_is_allowed(self) -> None:
        client = Client(headers={"Authorization": "Bearer " + settings.WORKER_TOKEN})
        response = client.get("/internal/ping")
        assert response.status_code == 200
        assert response.content == b"pong"

    @pytest.mark.parametrize("zone", ["admin", "public", ""])
    def test_any_zone_header_is_refused(self, zone: str) -> None:
        # A request that came through the edge must never reach the judge API, even with the token.
        client = Client(
            headers={"X-Edge-Zone": zone, "Authorization": "Bearer " + settings.WORKER_TOKEN}
        )
        assert client.get("/internal/ping").status_code == 404

    @pytest.mark.parametrize(
        "authorization",
        [
            None,
            "",
            "Bearer",
            "Bearer ",
            "bearer {token}",
            "Basic {token}",
            "Bearer {token}x",
            "Bearer x{token}",
            "Bearer  {token}",
            "Bearer {token} ",
            "Token {token}",
        ],
    )
    def test_wrong_or_missing_token_is_refused(self, authorization: str | None) -> None:
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization.format(token=settings.WORKER_TOKEN)
        assert Client(headers=headers).get("/internal/ping").status_code == 404


# ---- sessions are bound to the role that made them ----


def session_cookie(client: Client) -> str:
    return client.cookies[settings.SESSION_COOKIE_NAME].value


@pytest.mark.django_db
@pytest.mark.parametrize(("made_in", "used_in"), [("admin", "public"), ("public", "admin")])
def test_session_from_the_other_role_is_rejected(
    settings: SettingsWrapper, made_in: str, used_in: str
) -> None:
    user = User.objects.create_user(username="alice", is_staff=True)
    use_role(settings, made_in, WHOAMI)
    first = Client(headers={"X-Edge-Zone": made_in})
    first.force_login(user)
    assert first.get("/whoami").content == b"alice"
    stolen = session_cookie(first)

    use_role(settings, used_in, WHOAMI)
    second = Client(headers={"X-Edge-Zone": used_in})
    second.cookies[settings.SESSION_COOKIE_NAME] = stolen
    assert second.get("/whoami").content == b"anonymous"
    # The stolen session is gone, not just ignored.
    assert not SessionStore().exists(stolen)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["public", "admin"])
def test_session_from_the_same_role_still_works(settings: SettingsWrapper, role: str) -> None:
    user = User.objects.create_user(username="alice", is_staff=True)
    use_role(settings, role, WHOAMI)
    client = Client(headers={"X-Edge-Zone": role})
    client.force_login(user)
    assert client.get("/whoami").content == b"alice"
    assert client.get("/whoami").content == b"alice"


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["public", "admin"])
def test_anonymous_sessions_are_bound_too(settings: SettingsWrapper, role: str) -> None:
    # Sign-in state (the OAuth state and nonce) lives in an anonymous session: bind it as well.
    use_role(settings, role, WHOAMI)
    client = Client(headers={"X-Edge-Zone": role})
    client.get("/whoami")
    key = session_cookie(client)
    assert SessionStore(session_key=key).get("_role") == role


@pytest.mark.django_db
def test_session_without_a_role_is_rejected(settings: SettingsWrapper) -> None:
    use_role(settings, "public", WHOAMI)
    store = SessionStore()
    store["seen"] = True
    store.create()
    client = Client(headers={"X-Edge-Zone": "public"})
    client.cookies[settings.SESSION_COOKIE_NAME] = store.session_key or ""
    client.get("/whoami")
    assert not SessionStore().exists(store.session_key or "")


@pytest.mark.parametrize("role", ["public", "admin"])
def test_each_role_has_its_own_cookie_names(settings: SettingsWrapper, role: str) -> None:
    from config import env

    names = env.role_cookie_names(role)
    other = env.role_cookie_names("admin" if role == "public" else "public")
    assert names["SESSION_COOKIE_NAME"] != other["SESSION_COOKIE_NAME"]
    assert names["CSRF_COOKIE_NAME"] != other["CSRF_COOKIE_NAME"]
    assert (
        names
        == {
            "public": {"SESSION_COOKIE_NAME": "g_sess", "CSRF_COOKIE_NAME": "g_csrf"},
            "admin": {"SESSION_COOKIE_NAME": "g_admin", "CSRF_COOKIE_NAME": "g_admin_csrf"},
        }[role]
    )
