"""Google sign-in through the real dev stack: edge, public web role, :8090 and the fake provider.

The unit tests (web/tests/test_google_signin.py) cover the refusals; this checks that the pieces
really fit: real redirects, real RS256 tokens, the token and key requests through the edge.
"""

import re
import secrets
from urllib.parse import parse_qs, urlsplit

import requests

PUBLIC = "http://127.0.0.1:8000"


class LocalBrowser(requests.Session):
    """Behaves like a browser on this machine.

    The public site's cookies are Secure. Browsers still send them to http://127.0.0.1 (a local
    address counts as secure); Python's cookie jar doesn't, so they are relaxed before each request.
    """

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        for cookie in self.cookies:
            cookie.secure = False
        return super().prepare_request(request)


def visitor() -> requests.Session:
    """A browser with its own address, so test runs don't use up each other's rate limits.

    In production Cloudflare sets Cf-Connecting-Ip to each visitor's address; here we play it.
    """
    http = LocalBrowser()
    http.headers["Cf-Connecting-Ip"] = f"198.51.100.{secrets.randbelow(254) + 1}"
    return http


def follow_to_provider(http: requests.Session) -> str:
    """Start sign-in and return the provider's sign-in page URL."""
    response = http.get(f"{PUBLIC}/auth/google/start?next=/privacy", allow_redirects=False)
    assert response.status_code == 302, response.text
    location = response.headers["Location"]
    assert location.startswith("http://127.0.0.1:8099/authorize?")
    return location


def pick_user(http: requests.Session, authorize_url: str, sub: str) -> str:
    """Choose a test account on the provider's page; return the callback URL it sends us to."""
    page = http.get(authorize_url)
    assert page.status_code == 200
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', page.text))
    fields.update({"sub": "", "other_sub": sub})
    response = http.post("http://127.0.0.1:8099/authorize", data=fields, allow_redirects=False)
    assert response.status_code == 302
    return response.headers["Location"]


def test_new_account_signs_in_through_the_stack() -> None:
    http = visitor()
    sub = "flow-test-" + secrets.token_hex(6)
    callback = pick_user(http, follow_to_provider(http), sub)
    assert parse_qs(urlsplit(callback).query)["code"]

    response = http.get(callback, allow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers["Location"] == "/auth/welcome"

    form = http.get(f"{PUBLIC}/auth/welcome")
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form.text)
    assert csrf
    username = "flow_" + secrets.token_hex(4)
    response = http.post(
        f"{PUBLIC}/auth/welcome",
        data={
            "csrfmiddlewaretoken": csrf.group(1),
            "username": username,
            "display_name": "Flow Test",
            "accept_privacy": "on",
        },
        headers={"Referer": f"{PUBLIC}/auth/welcome"},
        allow_redirects=False,
    )
    assert response.status_code == 302, response.text
    assert response.headers["Location"] == "/privacy"
    assert "g_sess" in http.cookies

    # Signing in again with the same Google account goes straight in.
    again = visitor()
    callback = pick_user(again, follow_to_provider(again), sub)
    response = again.get(callback, allow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == "/privacy"
