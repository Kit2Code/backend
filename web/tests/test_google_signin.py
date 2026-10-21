"""Google sign-in on the public role (OpenID Connect authorization-code flow with PKCE).

Google itself is replaced by `FakeGoogle`, which answers the token and key requests at the HTTP
layer (allowed: these are unit tests). It signs real RS256 ID tokens, so the signature and claim
checks run for real. The dev stack's fake provider covers the same flow end to end (e2e tests).
"""

import base64
import hashlib
import hmac
import io
import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import urllib3
from django.core.management import call_command
from django.test import Client
from joserfc import jwt
from joserfc.jwk import RSAKey
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import GoogleIdentity, User
from core import crypto
from core.models import SiteSetting
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

CLIENT_ID = "test-client.apps.example"
CLIENT_SECRET = "test-client-secret"
GOOGLE = "https://accounts.google.com"
TOKEN_URL = "http://edge:8090/token"
JWKS_URL = "http://edge:8090/certs"
SUB = "110000000000000000042"


class FakeGoogle:
    """Answers POST /token and GET /certs like Google would, and records what it was sent."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
        self.signing_key: Any = self.key
        self.header: dict[str, Any] = {"alg": "RS256", "kid": "k1"}
        self.claims: dict[str, Any] = {}
        self.token_requests: list[dict[str, Any]] = []
        self.jwks_requests = 0
        self.token_status = 200
        self.token_body: bytes | None = None  # raw override

    def id_token(self) -> str:
        return jwt.encode(self.header, self.claims, self.signing_key)

    def reply(self, request: requests.PreparedRequest) -> tuple[int, bytes]:
        if request.url == TOKEN_URL and request.method == "POST":
            body = request.body.decode() if isinstance(request.body, bytes) else request.body
            form = {k: v[0] for k, v in parse_qs(body or "").items()}
            self.token_requests.append(
                {"form": form, "authorization": request.headers.get("Authorization", "")}
            )
            if self.token_body is not None:
                return self.token_status, self.token_body
            token = {"access_token": "at", "token_type": "Bearer", "id_token": self.id_token()}
            return self.token_status, json.dumps(token).encode()
        if request.url == JWKS_URL and request.method == "GET":
            self.jwks_requests += 1
            return 200, json.dumps({"keys": [self.key.as_dict(private=False)]}).encode()
        return 404, b"not found"


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> Iterator[FakeGoogle]:
    """Configure sign-in and route its HTTP requests to a FakeGoogle."""
    settings.GOOGLE_TOKEN_URL = TOKEN_URL
    settings.GOOGLE_JWKS_URL = JWKS_URL
    settings.GOOGLE_ISSUERS = [GOOGLE, "accounts.google.com"]
    setting = SiteSetting.get()
    setting.google_client_id = CLIENT_ID
    setting.google_client_secret_encrypted = crypto.encrypt_secret(CLIENT_SECRET)
    setting.save()
    fake = FakeGoogle()

    def send(
        adapter: requests.adapters.HTTPAdapter, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        status, body = fake.reply(request)
        raw = urllib3.HTTPResponse(
            body=io.BytesIO(body),
            headers={"Content-Type": "application/json"},
            status=status,
            preload_content=False,
        )
        return adapter.build_response(request, raw)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    yield fake


def start(client: Client, next_url: str | None = None) -> dict[str, str]:
    """Begin sign-in; return the query parameters of the redirect to Google."""
    query = {"next": next_url} if next_url is not None else {}
    response = client.get("/auth/google/start", query)
    assert response.status_code == 302, response.content
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    return {k: v[0] for k, v in parse_qs(location.query).items()}


def good_claims(params: dict[str, str], **changes: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "iss": GOOGLE,
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": SUB,
        "iat": now,
        "exp": now + 3600,
        "nonce": params["nonce"],
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def sign_in(client: Client, google: FakeGoogle, next_url: str | None = None, **changes: Any) -> Any:
    """Run start + callback with an ID token made from good claims plus `changes`."""
    params = start(client, next_url)
    google.claims = good_claims(params, **changes)
    return client.get("/auth/google/callback", {"state": params["state"], "code": "the-code"})


def welcome(client: Client, **fields: str) -> Any:
    data = {
        "username": "alice",
        "display_name": "Alice A.",
        "school": "",
        "accept_privacy": "on",
    }
    data.update(fields)
    return client.post("/auth/welcome", data)


def signed_in_user(client: Client) -> str | None:
    user_id = client.session.get("_auth_user_id")
    return User.objects.get(pk=user_id).username if user_id else None


def make_member(username: str = "alice", sub: str = SUB, issuer: str = GOOGLE) -> User:
    user = User.objects.create_user(username=username)
    GoogleIdentity.objects.create(user=user, issuer=issuer, subject=sub)
    return user


# ---- the start of sign-in ----


def test_not_configured_says_so_and_does_not_redirect() -> None:
    response = EdgeClient().get("/auth/google/start")
    assert response.status_code == 200
    # No sign-in link anywhere on the page (the language switch's hidden "next" field does hold
    # this page's own path, which is fine).
    assert b'href="/auth/google/start' not in response.content
    assert "data-signin-unavailable" in response.content.decode()


def test_start_redirects_with_state_nonce_and_pkce(google: FakeGoogle) -> None:
    client = EdgeClient()
    params = start(client)
    assert params["response_type"] == "code"
    assert params["scope"] == "openid"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == "http://127.0.0.1:8000/auth/google/callback"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 32 and len(params["nonce"]) >= 32
    assert "client_secret" not in params
    # The challenge is the SHA-256 of a verifier that stays in our session, never in the URL.
    pending = client.session["google_signin"]
    digest = hashlib.sha256(pending["verifier"].encode()).digest()
    assert params["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert pending["verifier"] not in str(params)


def test_each_start_gets_new_values(google: FakeGoogle) -> None:
    client = EdgeClient()
    first, second = start(client), start(client)
    assert first["state"] != second["state"]
    assert first["nonce"] != second["nonce"]
    assert first["code_challenge"] != second["code_challenge"]


def test_start_is_rate_limited(google: FakeGoogle) -> None:
    client = EdgeClient()
    for _ in range(20):
        assert client.get("/auth/google/start").status_code == 302
    assert client.get("/auth/google/start").status_code == 429


# ---- the callback ----


def test_new_google_account_goes_to_the_first_sign_in_form(google: FakeGoogle) -> None:
    client = EdgeClient()
    response = sign_in(client, google)
    assert response.status_code == 302
    assert response["Location"] == "/auth/welcome"
    assert signed_in_user(client) is None
    assert not User.objects.exists()
    # The token request went through the edge, with the PKCE verifier and our credentials.
    (sent,) = google.token_requests
    assert sent["form"]["code"] == "the-code"
    assert sent["form"]["grant_type"] == "authorization_code"
    assert sent["form"]["redirect_uri"] == "http://127.0.0.1:8000/auth/google/callback"
    assert len(sent["form"]["code_verifier"]) >= 43
    assert (
        sent["authorization"]
        == "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    )
    assert google.jwks_requests == 1


def test_first_sign_in_creates_the_account(google: FakeGoogle) -> None:
    client = EdgeClient()
    sign_in(client, google, next_url="/problems/aplusb")
    response = welcome(client, username="alice_01", display_name="Alice", school="A School")
    assert response.status_code == 302
    assert response["Location"] == "/problems/aplusb"
    user = User.objects.get(username="alice_01")
    assert (user.display_name, user.school) == ("Alice", "A School")
    assert not user.has_usable_password()
    identity = GoogleIdentity.objects.get(user=user)
    assert (identity.issuer, identity.subject) == (GOOGLE, SUB)
    assert signed_in_user(client) == "alice_01"
    assert "google_pending" not in client.session


def test_returning_member_is_signed_in_and_the_session_key_rotates(google: FakeGoogle) -> None:
    make_member()
    client = EdgeClient()
    params = start(client)
    before = client.session.session_key
    google.claims = good_claims(params)
    response = client.get("/auth/google/callback", {"state": params["state"], "code": "c"})
    assert response.status_code == 302
    assert response["Location"] == "/"
    assert signed_in_user(client) == "alice"
    assert client.session.session_key != before
    assert GoogleIdentity.objects.get().last_login_at is not None


def test_issuer_without_https_is_the_same_google(google: FakeGoogle) -> None:
    # Google documents both forms of its issuer; both mean the same account.
    make_member()
    client = EdgeClient()
    sign_in(client, google, iss="accounts.google.com")
    assert signed_in_user(client) == "alice"


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "someone-else.apps.example"},
        {"aud": ["someone-else.apps.example"]},
        {"aud": [CLIENT_ID, "someone-else.apps.example"], "azp": "someone-else.apps.example"},
        {"aud": [CLIENT_ID, "someone-else.apps.example"], "azp": None},
        {"azp": "someone-else.apps.example"},
        {"iss": "https://evil.example"},
        {"iss": "https://accounts.google.com.evil.example"},
        {"nonce": "not-the-nonce"},
        {"nonce": None},
        {"exp": int(time.time()) - 600},
        {"exp": None},
        {"iat": int(time.time()) + 3600},
        {"iat": None},
        {"sub": None},
        {"sub": ""},
        {"aud": None},
        {"iss": None},
    ],
    ids=repr,
)
def test_bad_id_tokens_are_refused(google: FakeGoogle, changes: dict[str, Any]) -> None:
    make_member()
    client = EdgeClient()
    response = sign_in(client, google, **changes)
    assert response.status_code == 400
    assert signed_in_user(client) is None
    assert "google_pending" not in client.session


def test_a_signature_from_another_key_is_refused(google: FakeGoogle) -> None:
    make_member()
    google.signing_key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    client = EdgeClient()
    assert sign_in(client, google).status_code == 400
    assert signed_in_user(client) is None


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_hs256_signed_with_the_public_key_is_refused(google: FakeGoogle) -> None:
    # The classic "algorithm confusion" trick: HMAC with the RSA public key as the secret.
    # joserfc won't even build such a key, so the attacker's token is made by hand here.
    make_member()
    client = EdgeClient()
    params = start(client)
    header = b64url(json.dumps({"alg": "HS256", "kid": "k1"}).encode())
    payload = b64url(json.dumps(good_claims(params)).encode())
    secret = google.key.as_pem(private=False)
    mac = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    forged = f"{header}.{payload}.{b64url(mac)}"
    google.token_body = json.dumps({"access_token": "a", "id_token": forged}).encode()
    response = client.get("/auth/google/callback", {"state": params["state"], "code": "c"})
    assert response.status_code == 400
    assert signed_in_user(client) is None


def test_unsigned_token_is_refused(google: FakeGoogle) -> None:
    make_member()
    client = EdgeClient()
    params = start(client)

    unsigned = (
        b64url(json.dumps({"alg": "none"}).encode())
        + "."
        + b64url(json.dumps(good_claims(params)).encode())
        + "."
    )
    google.token_body = json.dumps({"access_token": "a", "id_token": unsigned}).encode()
    response = client.get("/auth/google/callback", {"state": params["state"], "code": "c"})
    assert response.status_code == 400
    assert signed_in_user(client) is None


@pytest.mark.parametrize("state", ["", "wrong-state", None])
def test_wrong_or_missing_state_is_refused(google: FakeGoogle, state: str | None) -> None:
    make_member()
    client = EdgeClient()
    params = start(client)
    google.claims = good_claims(params)
    query = {"code": "c"} if state is None else {"code": "c", "state": state}
    assert client.get("/auth/google/callback", query).status_code == 400
    assert signed_in_user(client) is None
    assert google.token_requests == []


def test_state_is_used_only_once(google: FakeGoogle) -> None:
    make_member()
    client = EdgeClient()
    params = start(client)
    google.claims = good_claims(params)
    query = {"state": params["state"], "code": "c"}
    assert client.get("/auth/google/callback", query).status_code == 302
    client.post("/auth/logout")
    assert client.get("/auth/google/callback", query).status_code == 400
    assert signed_in_user(client) is None


def test_state_from_another_browser_is_refused(google: FakeGoogle) -> None:
    # Login CSRF: an attacker's own sign-in link must not work in the victim's browser.
    make_member()
    attacker, victim = EdgeClient(), EdgeClient()
    params = start(attacker)
    google.claims = good_claims(params)
    start(victim)
    query = {"state": params["state"], "code": "c"}
    assert victim.get("/auth/google/callback", query).status_code == 400
    assert signed_in_user(victim) is None


def test_old_sign_in_attempt_expires(google: FakeGoogle, monkeypatch: pytest.MonkeyPatch) -> None:
    make_member()
    client = EdgeClient()
    params = start(client)
    google.claims = good_claims(params)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 11 * 60)
    response = client.get("/auth/google/callback", {"state": params["state"], "code": "c"})
    assert response.status_code == 400


def test_provider_error_is_shown_as_a_failed_sign_in(google: FakeGoogle) -> None:
    client = EdgeClient()
    params = start(client)
    query = {"state": params["state"], "error": "access_denied"}
    assert client.get("/auth/google/callback", query).status_code == 400
    assert google.token_requests == []


def test_token_endpoint_error_is_a_failed_sign_in(google: FakeGoogle) -> None:
    client = EdgeClient()
    google.token_status = 400
    google.token_body = b'{"error": "invalid_grant"}'
    assert sign_in(client, google).status_code == 400


def test_oversized_token_response_is_refused(google: FakeGoogle) -> None:
    client = EdgeClient()
    google.token_body = json.dumps({"id_token": "x" * 70_000}).encode()
    assert sign_in(client, google).status_code == 400


def test_disabled_user_is_refused(google: FakeGoogle) -> None:
    user = make_member()
    user.is_active = False
    user.save()
    client = EdgeClient()
    response = sign_in(client, google)
    assert response.status_code == 403
    assert signed_in_user(client) is None


def test_closed_registration_refuses_new_accounts_only(google: FakeGoogle) -> None:
    setting = SiteSetting.get()
    setting.registration_open = False
    setting.save()
    client = EdgeClient()
    response = sign_in(client, google)
    assert response.status_code == 403
    assert "google_pending" not in client.session
    # Existing members still get in.
    make_member("bob", sub="other-sub")
    other = EdgeClient()
    sign_in(other, google, sub="other-sub")
    assert signed_in_user(other) == "bob"


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/",
        "//evil.example/",
        "///evil.example/",
        "/\\evil.example/",
        "\\\\evil.example",
        "http:/evil.example",
        "javascript:alert(1)",
        "evil.example",
        " /x",
        "/x\r\nSet-Cookie: a=b",
    ],
)
def test_unsafe_next_is_ignored(google: FakeGoogle, next_url: str) -> None:
    make_member()
    client = EdgeClient()
    response = sign_in(client, google, next_url=next_url)
    assert response.status_code == 302
    assert response["Location"] == "/"


def test_safe_next_is_kept(google: FakeGoogle) -> None:
    make_member()
    client = EdgeClient()
    response = sign_in(client, google, next_url="/problems/aplusb?tab=2")
    assert response["Location"] == "/problems/aplusb?tab=2"


# ---- the first sign-in form ----


def test_welcome_without_a_pending_identity_goes_back_to_sign_in() -> None:
    response = EdgeClient().get("/auth/welcome")
    assert response.status_code == 302
    assert response["Location"] == "/auth/google/start"


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"username": "Al"}, "username"),
        ({"username": "Alice"}, "username"),
        ({"username": "a" * 21}, "username"),
        ({"display_name": ""}, "display_name"),
        ({"display_name": "Eve‮"}, "display_name"),
        ({"school": "School\x00"}, "school"),
        ({"accept_privacy": ""}, "accept_privacy"),
    ],
)
def test_welcome_form_checks_its_fields(
    google: FakeGoogle, fields: dict[str, str], error_field: str
) -> None:
    client = EdgeClient()
    sign_in(client, google)
    response = welcome(client, **fields)
    assert response.status_code == 200
    assert error_field in response.context["form"].errors
    assert not User.objects.exists()


def test_welcome_refuses_a_taken_username(google: FakeGoogle) -> None:
    User.objects.create_user(username="alice")
    client = EdgeClient()
    sign_in(client, google)
    response = welcome(client, username="alice")
    assert response.status_code == 200
    assert "username" in response.context["form"].errors
    assert User.objects.count() == 1


def test_welcome_pending_identity_expires(
    google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = EdgeClient()
    sign_in(client, google)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 11 * 60)
    response = welcome(client)
    assert response.status_code == 302
    assert response["Location"] == "/auth/google/start"
    assert not User.objects.exists()


def test_welcome_is_rate_limited(google: FakeGoogle) -> None:
    client = EdgeClient()
    sign_in(client, google)
    for _ in range(5):
        assert welcome(client, username="Bad").status_code == 200
    assert welcome(client).status_code == 429
    assert not User.objects.exists()


def test_welcome_respects_closed_registration(google: FakeGoogle) -> None:
    client = EdgeClient()
    sign_in(client, google)
    setting = SiteSetting.get()
    setting.registration_open = False
    setting.save()
    assert welcome(client).status_code == 403
    assert not User.objects.exists()


def test_welcome_needs_csrf(google: FakeGoogle) -> None:
    client = EdgeClient(enforce_csrf_checks=True)
    sign_in(client, google)
    assert welcome(client).status_code == 403


def test_identity_linked_meanwhile_signs_in_instead(google: FakeGoogle) -> None:
    # Two tabs finish the first sign-in for the same Google account: the second one just signs in.
    client = EdgeClient()
    sign_in(client, google)
    make_member("bob")
    response = welcome(client, username="carol")
    assert response.status_code == 302
    assert signed_in_user(client) == "bob"
    assert not User.objects.filter(username="carol").exists()


# ---- the stored client secret ----


def test_client_secret_is_stored_encrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("my-secret-value\n"))
    call_command("set_google_client", "--client-id", "abc.apps.example")
    setting = SiteSetting.get()
    assert setting.google_client_id == "abc.apps.example"
    assert "my-secret-value" not in setting.google_client_secret_encrypted
    assert crypto.decrypt_secret(setting.google_client_secret_encrypted) == "my-secret-value"


def test_a_different_secret_key_cannot_decrypt(settings: SettingsWrapper) -> None:
    token = crypto.encrypt_secret("my-secret-value")
    settings.SECRET_KEY = "another-secret-key-" + "x" * 50
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt_secret(token)


def test_undecryptable_secret_means_not_configured(google: FakeGoogle) -> None:
    setting = SiteSetting.get()
    setting.google_client_secret_encrypted = "garbage"
    setting.save()
    response = EdgeClient().get("/auth/google/start")
    assert response.status_code == 200
    assert "data-signin-unavailable" in response.content.decode()


def test_privacy_page() -> None:
    response = EdgeClient().get("/privacy")
    assert response.status_code == 200
