"""Sign in with Google: OpenID Connect authorization-code flow with PKCE (public role only).

1. `begin()` makes a random state, nonce and PKCE verifier, keeps them in the session, and gives
   the URL of Google's sign-in page (asking for the `openid profile` scopes: `profile` is what
   gives the ID token a `picture` claim).
2. Google sends the browser back to our callback with `state` and a one-time `code`.
3. `finish()` checks the state, swaps the code (plus the verifier and our client secret) for an
   ID token through the edge's :8090 listener, and verifies the token: RS256 signature with
   Google's keys (also via :8090), issuer, audience, expiry, issue time and nonce.
4. The result is Google's issuer and account id (`sub`), plus the photo address (`picture`). We
   never ask for email, and the name Google sends with `profile` is ignored, never stored.

See docs/ARCHITECTURE.md, "Accounts and sign-in". Authlib builds the requests; joserfc (the JOSE
library Authlib itself uses) checks the token.
"""

import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import requests
from authlib.integrations.requests_client import OAuth2Session
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.utils.http import url_has_allowed_host_and_scheme
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from core import crypto
from core.models import SiteSetting

logger = logging.getLogger(__name__)

SIGNIN_SESSION_KEY = "google_signin"  # state, nonce, verifier, next: one attempt
PENDING_SESSION_KEY = "google_pending"  # a new Google account waiting for the first sign-in form
ATTEMPT_LIFETIME_S = 10 * 60
HTTP_TIMEOUT_S = 10
MAX_RESPONSE_BYTES = 64 * 1024
CLOCK_LEEWAY_S = 60


class SignInError(Exception):
    """Sign-in failed. The message is for the log only, never shown to the user."""


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class GoogleAccount:
    """Who signed in, as far as we are concerned: Google's issuer and account id, and the address
    of their profile photo ("" if none; checked again before use, in accounts.avatars)."""

    issuer: str
    subject: str
    picture: str = ""


def load_config() -> GoogleConfig | None:
    """The client ID and secret from the admin settings, or None if sign-in isn't set up."""
    setting = SiteSetting.get()
    if not setting.google_client_id or not setting.google_client_secret_encrypted:
        return None
    try:
        secret = crypto.decrypt_secret(setting.google_client_secret_encrypted)
    except crypto.DecryptError:
        # For example a backup restored onto a new install: the secret must be typed again.
        logger.warning("Google client secret can't be decrypted; sign-in is off until it's re-set")
        return None
    return GoogleConfig(client_id=setting.google_client_id, client_secret=secret)


def safe_next(value: str | None) -> str:
    """Return `value` if it is a path on this site, otherwise "/"."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith(("//", "/\\"))
        or any(ord(ch) < 0x20 or ch == "\x7f" for ch in value)
        or not url_has_allowed_host_and_scheme(value, allowed_hosts=None)
    ):
        return "/"
    return value


def _oauth_session(config: GoogleConfig) -> OAuth2Session:
    session = OAuth2Session(
        client_id=config.client_id,
        client_secret=config.client_secret,
        scope="openid profile",
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        code_challenge_method="S256",
    )
    # Ignore proxy settings and ~/.netrc from the environment: talk to the edge directly.
    session.trust_env = False
    return session


def begin(config: GoogleConfig, session: SessionBase, next_url: str | None) -> str:
    """Start an attempt: remember its secrets in the session and return Google's sign-in URL."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 characters; PKCE allows 43 to 128
    url, _state = _oauth_session(config).create_authorization_url(
        settings.GOOGLE_AUTHORIZE_URL, state=state, code_verifier=verifier, nonce=nonce
    )
    session[SIGNIN_SESSION_KEY] = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "next": safe_next(next_url),
        "started": time.time(),
    }
    return str(url)


def take_attempt(session: SessionBase, state: str) -> dict[str, Any]:
    """Remove the attempt from the session (it is used once) and check its state and age."""
    attempt = session.pop(SIGNIN_SESSION_KEY, None)
    if not isinstance(attempt, dict):
        raise SignInError("no sign-in attempt in this session")
    if not state or not hmac.compare_digest(state.encode(), str(attempt["state"]).encode()):
        raise SignInError("state does not match")
    if time.time() - float(attempt["started"]) > ATTEMPT_LIFETIME_S:
        raise SignInError("sign-in attempt expired")
    return attempt


def _read_capped(response: requests.Response) -> bytes:
    """Read a streamed response body, refusing anything over MAX_RESPONSE_BYTES."""
    body = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
    response.close()
    if len(body) > MAX_RESPONSE_BYTES:
        raise SignInError(f"response from {response.url} is over {MAX_RESPONSE_BYTES} bytes")
    return bytes(body)


def _cap_token_response(response: requests.Response) -> requests.Response:
    """Authlib hook: load the (streamed) token response with the size cap before it is parsed."""
    # `_content` is where requests keeps a loaded body; filling it ourselves is how we read it
    # with a limit instead of all at once.
    response._content = _read_capped(response)
    return response


def fetch_id_token(config: GoogleConfig, code: str, verifier: str) -> str:
    """Swap the authorization code for tokens (through the edge) and return the ID token."""
    oauth = _oauth_session(config)
    oauth.register_compliance_hook("access_token_response", _cap_token_response)
    try:
        token = oauth.fetch_token(
            settings.GOOGLE_TOKEN_URL,
            code=code,
            code_verifier=verifier,
            grant_type="authorization_code",
            timeout=HTTP_TIMEOUT_S,
            stream=True,
            allow_redirects=False,
        )
    except SignInError:
        raise
    except Exception as error:  # network errors, OAuth errors, bad JSON: all just "failed"
        raise SignInError(f"token request failed: {type(error).__name__}") from None
    id_token = token.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise SignInError("token response has no id_token")
    return id_token


def fetch_signing_keys() -> KeySet:
    """Google's current public signing keys (JWKS), through the edge."""
    with requests.Session() as http:
        http.trust_env = False
        try:
            response = http.get(
                settings.GOOGLE_JWKS_URL,
                timeout=HTTP_TIMEOUT_S,
                stream=True,
                allow_redirects=False,
            )
            body = _read_capped(response)
            if response.status_code != 200:
                raise SignInError(f"keys request answered {response.status_code}")
            return KeySet.import_key_set(json.loads(body))
        except SignInError:
            raise
        except Exception as error:
            raise SignInError(f"keys request failed: {type(error).__name__}") from None


def verify_id_token(config: GoogleConfig, id_token: str, nonce: str, keys: KeySet) -> GoogleAccount:
    """Check the ID token's signature and claims; return the account it is about."""
    try:
        # Only RS256: an "alg: none" or HMAC-signed token is refused before any claim is read.
        token = jwt.decode(id_token, keys, algorithms=["RS256"])
        registry = jwt.JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY_S,
            iss={"essential": True, "values": settings.GOOGLE_ISSUERS},
            aud={"essential": True, "value": config.client_id},
            exp={"essential": True},
            iat={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        registry.validate(token.claims)
    except JoseError as error:
        raise SignInError(f"ID token refused: {type(error).__name__}") from None
    claims = token.claims
    # OpenID Connect Core 3.1.3.7: with several audiences, `azp` must be us; if present, always.
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    azp = claims.get("azp")
    if (len(audiences) > 1 and azp is None) or (azp is not None and azp != config.client_id):
        raise SignInError("ID token is meant for another client (azp)")
    picture = claims.get("picture")
    # Google's two issuer spellings are the same issuer: store one form.
    return GoogleAccount(
        issuer=settings.GOOGLE_ISSUERS[0],
        subject=str(claims["sub"]),
        picture=picture if isinstance(picture, str) else "",
    )


def finish(
    config: GoogleConfig, session: SessionBase, query: dict[str, str]
) -> tuple[GoogleAccount, str]:
    """Handle the callback query. Return the account and where to go next, or raise SignInError."""
    attempt = take_attempt(session, query.get("state", ""))
    if "error" in query:
        raise SignInError(f"provider says: {query['error'][:100]}")
    code = query.get("code", "")
    if not code:
        raise SignInError("no code in callback")
    id_token = fetch_id_token(config, code, str(attempt["verifier"]))
    account = verify_id_token(config, id_token, str(attempt["nonce"]), fetch_signing_keys())
    return account, str(attempt["next"])


def remember_pending(session: SessionBase, account: GoogleAccount, next_url: str) -> None:
    """Keep a new Google account in the session until the first sign-in form is sent."""
    session[PENDING_SESSION_KEY] = {
        "issuer": account.issuer,
        "subject": account.subject,
        "picture": account.picture,
        "next": next_url,
        "expires": time.time() + ATTEMPT_LIFETIME_S,
    }


def pending_account(session: SessionBase) -> tuple[GoogleAccount, str] | None:
    """The new Google account waiting for the first sign-in form, if it hasn't expired."""
    pending = session.get(PENDING_SESSION_KEY)
    if not isinstance(pending, dict):
        return None
    if time.time() > float(pending["expires"]):
        del session[PENDING_SESSION_KEY]
        return None
    account = GoogleAccount(
        str(pending["issuer"]), str(pending["subject"]), str(pending.get("picture", ""))
    )
    return account, str(pending["next"])
