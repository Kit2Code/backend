"""The admin site through the Cloudflare tunnel: check the Cloudflare Access token ourselves.

Cloudflare Access sits in front of the remote admin hostname and adds a signed token
(`Cf-Access-Jwt-Assertion`) to every request it lets through. The edge's :8082 listener marks those
requests with the zone "admin-remote". We accept that zone only if the token is valid: RS256 only,
signed by one of the Access team's keys (fetched through the edge's :8090 /access-certs), issued by
our team for our application (AUD tag), and current. Anything else, including a missing setting,
is treated like a wrong zone (404): if Access is ever switched off or misconfigured, the remote
admin closes instead of opening. See docs/ARCHITECTURE.md, "The admin site from outside".
"""

import json
import logging
import threading
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from django.http import HttpRequest
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet

logger = logging.getLogger(__name__)

REMOTE_ZONE = "admin-remote"
ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
MAX_TOKEN_LENGTH = 8 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
HTTP_TIMEOUT_S = 10
CLOCK_LEEWAY_S = 60
KEYS_LIFETIME_S = 60 * 60
# After a failed or unhelpful fetch, wait this long before asking Cloudflare again.
REFETCH_AFTER_S = 60


@dataclass(frozen=True)
class AccessUser:
    """Who Cloudflare Access let through."""

    email: str


class _KeyCache:
    """The team's keys: kept for an hour, fetched again early only when a token names a key id we
    don't have (Cloudflare rotates its keys), and then at most once a minute, so tokens with made-up
    key ids can't make us ask Cloudflare over and over."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: KeySet | None = None
        self._fetched_at: float | None = None
        self._refreshed_at: float | None = None

    def get(self) -> KeySet | None:
        with self._lock:
            now = time.monotonic()
            if self._fetched_at is None or (
                now - self._fetched_at > (KEYS_LIFETIME_S if self._keys else REFETCH_AFTER_S)
            ):
                self._fetch(now)
            return self._keys

    def refresh(self) -> KeySet | None:
        with self._lock:
            now = time.monotonic()
            if self._refreshed_at is None or now - self._refreshed_at > REFETCH_AFTER_S:
                self._refreshed_at = now
                self._fetch(now)
            return self._keys

    def _fetch(self, now: float) -> None:
        self._fetched_at = now
        fetched = _fetch_keys()
        if fetched is not None:
            self._keys = fetched

    def forget(self) -> None:
        with self._lock:
            self._keys = None
            self._fetched_at = None
            self._refreshed_at = None


_cache = _KeyCache()


def forget_keys() -> None:
    """Drop the cached keys (tests, and nothing else, need this)."""
    _cache.forget()


def configured() -> bool:
    return bool(settings.CF_ACCESS_TEAM_DOMAIN and settings.CF_ACCESS_AUD)


def _fetch_keys() -> KeySet | None:
    """The Access team's public keys, through the edge; None if they can't be had."""
    with requests.Session() as http:
        http.trust_env = False  # talk to the edge directly
        try:
            response = http.get(
                settings.CF_ACCESS_CERTS_URL,
                timeout=HTTP_TIMEOUT_S,
                stream=True,
                allow_redirects=False,
            )
            with response:
                body = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
                if response.status_code != 200 or len(body) > MAX_RESPONSE_BYTES:
                    logger.warning("Cloudflare Access keys: answer %s", response.status_code)
                    return None
            data = json.loads(body)
            # Only the "keys" list: Cloudflare also sends "public_cert(s)", which we don't need.
            return KeySet.import_key_set({"keys": data["keys"]})
        except (requests.RequestException, ValueError, KeyError, TypeError, JoseError) as error:
            logger.warning("Cloudflare Access keys: %s", type(error).__name__)
            return None


def _decode(token: str, keys: KeySet) -> dict[str, object]:
    """The token's claims, if its signature (RS256 only) and claims are right; else JoseError."""
    decoded = jwt.decode(token, keys, algorithms=["RS256"])
    registry = jwt.JWTClaimsRegistry(
        leeway=CLOCK_LEEWAY_S,
        iss={"essential": True, "value": f"https://{settings.CF_ACCESS_TEAM_DOMAIN}"},
        aud={"essential": True, "value": settings.CF_ACCESS_AUD},
        exp={"essential": True},
        iat={"essential": True},
    )
    registry.validate(decoded.claims)
    return dict(decoded.claims)


def verify(token: str) -> AccessUser | None:
    """The Access user for a valid token, or None (also when remote admin isn't configured)."""
    if not configured() or not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    keys = _cache.get()
    if keys is None:
        return None
    try:
        claims = _decode(token, keys)
    except InvalidKeyIdError:  # maybe a key Cloudflare added since we fetched: try fresh keys
        keys = _cache.refresh()
        if keys is None:
            return None
        try:
            claims = _decode(token, keys)
        except JoseError:
            return None
    except JoseError:
        return None
    return AccessUser(email=str(claims.get("email", "")))


def check(request: HttpRequest) -> bool:
    """True if this remote-admin request carries a valid Access token; remembers the user."""
    user = verify(request.headers.get(ACCESS_JWT_HEADER, ""))
    if user is None:
        return False
    request.cloudflare_access = user  # type: ignore[attr-defined]
    return True


def email_of(request: HttpRequest) -> str:
    """The Access user's email for a remote-admin request, else ""."""
    user = getattr(request, "cloudflare_access", None)
    return user.email if isinstance(user, AccessUser) else ""
