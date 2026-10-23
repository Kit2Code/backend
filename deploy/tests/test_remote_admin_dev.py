"""The admin site through the edge's :8082 (the Cloudflare tunnel's way in), in the dev stack.

The dev stack's fake-oidc stands in for Cloudflare Access: it serves an Access-style key set (the
edge's :8090 /access-certs forwards to it) and, for tests only, hands out signed Access tokens.
"""

import json
import urllib.error
import urllib.request

REMOTE_ADMIN = "http://127.0.0.1:8002"
FAKE = "http://127.0.0.1:8099"


def access_token(email: str = "owner@example.com", aud: str = "") -> str:
    query = f"?email={email}" + (f"&aud={aud}" if aud else "")
    with urllib.request.urlopen(f"{FAKE}/access-token{query}", timeout=10) as response:  # noqa: S310
        return str(json.loads(response.read())["token"])


def status(path: str, token: str | None = None) -> int:
    headers = {"Cf-Access-Jwt-Assertion": token} if token is not None else {}
    request = urllib.request.Request(REMOTE_ADMIN + path, headers=headers)  # noqa: S310 - the dev stack
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


def test_with_an_access_token_the_admin_login_opens() -> None:
    assert status("/admin/login/", access_token()) == 200


def test_without_a_token_it_is_404() -> None:
    assert status("/admin/login/") == 404


def test_a_token_for_another_application_is_404() -> None:
    assert status("/admin/login/", access_token(aud="someone-else")) == 404


def test_a_tampered_token_is_404() -> None:
    header, payload, signature = access_token().split(".")
    assert status("/admin/login/", f"{header}.{payload}.{signature[:-4]}AAAA") == 404


def test_the_judge_api_stays_closed() -> None:
    assert status("/internal/ping", access_token()) == 404
