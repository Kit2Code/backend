"""A fake "Sign in with Google" for the dev stack and end-to-end tests. Never used in production.

It speaks the parts of OpenID Connect that the grader uses, in the same shapes as Google:

- GET  /authorize  a page with test users to pick from (Google would ask for a Google account),
                   then a redirect back to the site with a one-time `code` and the `state`
- POST /token      swaps the code for tokens. Checks the client secret, the redirect URI and the
                   PKCE code verifier, and returns an RS256-signed ID token carrying the `nonce`
- GET  /certs      the public signing key (JWKS)
- GET  /a/<id>=s256-c  a test user's profile photo (a small PNG), standing in for
                   lh3.googleusercontent.com behind the edge's /photo route. With the `profile`
                   scope the ID token's `picture` points there, and also carries a `name`, as
                   Google's does (the grader must ignore it).

Settings come from environment variables:
  FAKE_OIDC_ISSUER         the `iss` of the tokens, e.g. http://127.0.0.1:8099
  FAKE_OIDC_CLIENT_ID      the only client ID accepted
  FAKE_OIDC_SECRET_FILE    file holding the only client secret accepted
  FAKE_OIDC_REDIRECT_URIS  comma-separated redirect URIs that may be used
The signing key is made fresh at every start and kept in memory only.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey

PORT = 8099
CODE_LIFETIME_S = 60
TOKEN_LIFETIME_S = 3600
MAX_BODY = 16 * 1024
TEST_USERS = [("100000000000000000001", "Alice"), ("100000000000000000002", "Bob")]

ISSUER = os.environ["FAKE_OIDC_ISSUER"]
CLIENT_ID = os.environ["FAKE_OIDC_CLIENT_ID"]
CLIENT_SECRET = Path(os.environ["FAKE_OIDC_SECRET_FILE"]).read_text(encoding="utf-8").strip()
REDIRECT_URIS = {uri.strip() for uri in os.environ["FAKE_OIDC_REDIRECT_URIS"].split(",")}

KEY = RSAKey.generate_key(2048, parameters={"kid": "fake-" + secrets.token_hex(4), "use": "sig"})

# code -> what the token request must match. Codes are used once.
_codes: dict[str, dict[str, Any]] = {}
_codes_lock = threading.Lock()


def pkce_challenge(verifier: str) -> str:
    """The S256 code challenge for a verifier (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


PHOTO_HOST = "https://lh3.googleusercontent.com"


def photo_png(seed: str, size: int = 64) -> bytes:
    """A plain square PNG whose colour depends on `seed` (so test users look different)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    colour = hashlib.sha256(seed.encode()).digest()[:3]
    rows = b"".join(b"\x00" + colour * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def id_token(sub: str, nonce: str, scope: str = "openid") -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": sub,
        "nonce": nonce,
        "iat": now,
        "exp": now + TOKEN_LIFETIME_S,
    }
    if "profile" in scope.split():
        claims["picture"] = f"{PHOTO_HOST}/a/fake-{sub}=s96-c"
        claims["name"] = f"Fake Name {sub[-4:]}"
    return jwt.encode({"alg": "RS256", "kid": KEY.kid, "typ": "JWT"}, claims, KEY)


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-oidc"

    # ---- helpers ----

    def send(self, status: int, body: bytes, content_type: str, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        self.send(status, json.dumps(data).encode(), "application/json")

    def oauth_error(self, error: str, status: int = 400) -> None:
        self.send_json(status, {"error": error})

    def query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def form(self) -> dict[str, str] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        body = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(body).items()}

    # ---- routes ----

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send(200, b"ok", "text/plain")
        elif path == "/certs":
            self.send_json(200, {"keys": [{**KEY.as_dict(private=False), "alg": "RS256"}]})
        elif path == "/authorize":
            self.authorize_page(self.query())
        elif path.startswith("/a/fake-") and path.endswith("=s256-c"):
            self.send(200, photo_png(path), "image/png")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        form = self.form()
        if form is None:
            self.send(413, b"too large", "text/plain")
        elif path == "/authorize":
            self.authorize_choice(form)
        elif path == "/token":
            self.token(form)
        else:
            self.send(404, b"not found", "text/plain")

    def check_authorize_request(self, params: dict[str, str]) -> str | None:
        """Return an error text if the sign-in request is not one Google would accept."""
        if params.get("client_id") != CLIENT_ID:
            return "unknown client_id"
        if params.get("redirect_uri") not in REDIRECT_URIS:
            return "redirect_uri not registered"
        if params.get("response_type") != "code":
            return "response_type must be code"
        if "openid" not in params.get("scope", "").split():
            return "scope must include openid"
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            return "PKCE S256 is required by this fake"
        if not params.get("state") or not params.get("nonce"):
            return "state and nonce are required by this fake"
        return None

    def authorize_page(self, params: dict[str, str]) -> None:
        error = self.check_authorize_request(params)
        if error:
            self.send(400, html.escape(error).encode(), "text/plain; charset=utf-8")
            return
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
            for k, v in params.items()
        )
        buttons = "".join(
            f'<p><button name="sub" value="{sub}">{html.escape(name)} ({sub})</button></p>'
            for sub, name in TEST_USERS
        )
        page = (
            "<!doctype html><meta charset=utf-8><title>Fake Google sign-in</title>"
            "<h1>Fake Google sign-in (dev only)</h1>"
            f'<form method="post" action="/authorize">{hidden}{buttons}'
            '<p><label>Other account id <input name="other_sub"></label>'
            ' <button name="sub" value="">Use it</button></p></form>'
        )
        self.send(200, page.encode(), "text/html; charset=utf-8")

    def authorize_choice(self, form: dict[str, str]) -> None:
        error = self.check_authorize_request(form)
        sub = form.get("sub") or form.get("other_sub", "")
        if error or not sub:
            self.send(400, html.escape(error or "pick a user").encode(), "text/plain")
            return
        code = secrets.token_urlsafe(24)
        with _codes_lock:
            _codes[code] = {
                "redirect_uri": form["redirect_uri"],
                "challenge": form["code_challenge"],
                "nonce": form["nonce"],
                "sub": sub,
                "scope": form.get("scope", "openid"),
                "expires": time.time() + CODE_LIFETIME_S,
            }
        target = form["redirect_uri"] + "?" + urlencode({"code": code, "state": form["state"]})
        self.send(302, b"", "text/plain", Location=target)

    def client_secret_ok(self, form: dict[str, str]) -> bool:
        client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                client_id, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            except ValueError:
                return False
        return client_id == CLIENT_ID and hmac.compare_digest(secret, CLIENT_SECRET)

    def token(self, form: dict[str, str]) -> None:
        if not self.client_secret_ok(form):
            self.oauth_error("invalid_client", 401)
            return
        if form.get("grant_type") != "authorization_code":
            self.oauth_error("unsupported_grant_type")
            return
        with _codes_lock:
            grant = _codes.pop(form.get("code", ""), None)
        if (
            grant is None
            or grant["expires"] < time.time()
            or grant["redirect_uri"] != form.get("redirect_uri")
            or grant["challenge"] != pkce_challenge(form.get("code_verifier", ""))
        ):
            self.oauth_error("invalid_grant")
            return
        self.send_json(
            200,
            {
                "access_token": secrets.token_urlsafe(24),
                "token_type": "Bearer",
                "expires_in": TOKEN_LIFETIME_S,
                "scope": grant["scope"],
                "id_token": id_token(grant["sub"], grant["nonce"], grant["scope"]),
            },
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib's name
        # One short line per request, without query strings (they hold codes and state).
        status = args[1] if len(args) > 1 else ""
        print(f"fake-oidc: {self.command} {urlsplit(self.path).path} {status}")


if __name__ == "__main__":
    print(f"fake-oidc: issuer {ISSUER}, listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104 - in its container
