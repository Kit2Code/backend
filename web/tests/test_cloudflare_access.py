"""The admin site through the Cloudflare tunnel: zone "admin-remote" is accepted only with a valid
Cloudflare Access token, checked by the site itself, so a wrong or missing Access policy fails
closed. Cloudflare's key endpoint (through the edge's :8090) is faked at the HTTP layer."""

import io
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
import requests
import urllib3
from django.test import Client, RequestFactory
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core import cloudflare_access
from core.models import AuditLog
from core.ratelimit import client_ip
from problems.models import Problem, ProblemVersion
from tests.conftest import use_role

pytestmark = pytest.mark.django_db

TEAM = "myteam.cloudflareaccess.com"
AUD = "a" * 64
CERTS_URL = "http://edge:8090/access-certs"
EDGE_IP = "10.89.91.10"


class FakeAccess:
    """Cloudflare Access's key set, and how often it was asked for."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.served = [self.key]
        self.requests = 0
        self.status = 200

    def token(self, key: Any = None, header: dict[str, Any] | None = None, **changes: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": f"https://{TEAM}",
            "aud": [AUD],
            "email": "owner@example.com",
            "sub": "user-1",
            "iat": now,
            "nbf": now,
            "exp": now + 600,
        }
        claims.update(changes)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(header or {"alg": "RS256", "kid": "k1"}, claims, key or self.key)


@pytest.fixture
def access(settings: SettingsWrapper, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAccess]:
    use_role(settings, "admin")
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM
    settings.CF_ACCESS_AUD = AUD
    settings.CF_ACCESS_CERTS_URL = CERTS_URL
    settings.GRADER_EDGE_ADDRESSES = frozenset({EDGE_IP})
    cloudflare_access.forget_keys()
    fake = FakeAccess()

    def send(
        adapter: requests.adapters.HTTPAdapter, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        assert request.url == CERTS_URL, request.url
        fake.requests += 1
        keys = {"keys": [{**key.as_dict(private=False), "alg": "RS256"} for key in fake.served]}
        raw = urllib3.HTTPResponse(
            body=io.BytesIO(json.dumps(keys).encode()),
            headers={"Content-Type": "application/json"},
            status=fake.status,
            preload_content=False,
        )
        return adapter.build_response(request, raw)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    yield fake
    cloudflare_access.forget_keys()


def remote(token: str | None = None, **headers: str) -> Client:
    """A client as the edge's :8082 listener passes requests on."""
    sent = {"X-Edge-Zone": "admin-remote", **headers}
    if token is not None:
        sent["Cf-Access-Jwt-Assertion"] = token
    return Client(headers=sent, REMOTE_ADDR=EDGE_IP)


def status(client: Client, path: str = "/admin/login/") -> int:
    return client.get(path).status_code


# ---- accepted ----


def test_a_valid_token_reaches_the_admin_login(access: FakeAccess) -> None:
    assert status(remote(access.token())) == 200


def test_keys_are_fetched_once_and_cached(access: FakeAccess) -> None:
    for _ in range(3):
        assert status(remote(access.token())) == 200
    assert access.requests == 1


def test_a_new_key_is_fetched_when_cloudflare_rotates(access: FakeAccess) -> None:
    assert status(remote(access.token())) == 200
    rotated = RSAKey.generate_key(2048, parameters={"kid": "k2"})
    access.served = [access.key, rotated]
    token = access.token(key=rotated, header={"alg": "RS256", "kid": "k2"})
    assert status(remote(token)) == 200
    assert access.requests == 2


def test_the_lan_admin_needs_no_token(access: FakeAccess) -> None:
    lan = Client(headers={"X-Edge-Zone": "admin"}, REMOTE_ADDR=EDGE_IP)
    assert lan.get("/admin/login/").status_code == 200
    assert access.requests == 0


# ---- refused (404, like a wrong zone) ----


def test_no_token_is_404(access: FakeAccess) -> None:
    assert status(remote()) == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": ["another-application"]},
        {"aud": None},
        {"iss": "https://otherteam.cloudflareaccess.com"},
        {"iss": None},
        {"exp": int(time.time()) - 3600},
        {"exp": None},
        {"nbf": int(time.time()) + 3600},
    ],
)
def test_wrong_claims_are_404(access: FakeAccess, changes: dict[str, Any]) -> None:
    assert status(remote(access.token(**changes))) == 404


def test_a_token_signed_by_another_key_is_404(access: FakeAccess) -> None:
    stranger = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    assert status(remote(access.token(key=stranger))) == 404


def test_hmac_and_none_algorithms_are_404(access: FakeAccess) -> None:
    secret = OctKey.import_key(b"x" * 32)
    hmac_token = access.token(key=secret, header={"alg": "HS256", "kid": "k1"})
    assert status(remote(hmac_token)) == 404
    header = "eyJhbGciOiJub25lIn0"  # {"alg":"none"}
    payload = access.token().split(".")[1]
    assert status(remote(f"{header}.{payload}.")) == 404


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "x" * 10_000])
def test_malformed_tokens_are_404(access: FakeAccess, token: str) -> None:
    assert status(remote(token)) == 404


def test_unreachable_keys_fail_closed(access: FakeAccess) -> None:
    access.status = 502
    assert status(remote(access.token())) == 404


@pytest.mark.parametrize("missing", ["CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"])
def test_unconfigured_remote_admin_is_404(
    access: FakeAccess, settings: SettingsWrapper, missing: str
) -> None:
    setattr(settings, missing, "")
    assert status(remote(access.token())) == 404
    assert access.requests == 0


def test_the_judge_api_is_never_reachable_remotely(access: FakeAccess) -> None:
    assert status(remote(access.token()), "/internal/ping") == 404


def test_the_public_role_refuses_the_remote_zone(
    access: FakeAccess, settings: SettingsWrapper
) -> None:
    use_role(settings, "public")
    assert status(remote(access.token()), "/") == 404


# ---- who is asking ----


def test_client_ip_comes_from_cloudflare_for_remote_requests(access: FakeAccess) -> None:
    factory = RequestFactory()
    remote_request = factory.get(
        "/", REMOTE_ADDR=EDGE_IP,
        headers={"X-Edge-Zone": "admin-remote", "Cf-Connecting-Ip": "203.0.113.7",
                 "X-Forwarded-For": "10.89.92.5"},
    )  # fmt: skip
    assert client_ip(remote_request) == "203.0.113.7"
    lan_request = factory.get(
        "/", REMOTE_ADDR=EDGE_IP,
        headers={"X-Edge-Zone": "admin", "Cf-Connecting-Ip": "203.0.113.7",
                 "X-Forwarded-For": "192.168.1.20"},
    )  # fmt: skip
    assert client_ip(lan_request) == "192.168.1.20"  # a LAN client can't claim an address


def test_audit_rows_name_the_access_user(access: FakeAccess) -> None:
    problem = Problem.objects.create(slug="p")
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64, status="valid"
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    client = remote(access.token())
    client.force_login(User.objects.create_user(username="boss", is_staff=True, is_superuser=True))
    response = client.post(f"/admin/grader/problems/{problem.pk}/visibility", {"action": "open"})
    assert response.status_code == 302
    row = AuditLog.objects.get(action="problem.publish")
    assert row.details["access_email"] == "owner@example.com"
