"""A small rate limiter stored in Postgres, plus the client IP address it is keyed on.

Each (limit, key, window) has one counter row. One SQL statement adds 1 and returns the new count,
so it stays exact across gunicorn processes without Redis (see docs/DECISIONS.md).
"""

import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import connection
from django.http import HttpRequest
from django.utils import timezone

# The table of core.models.RateLimitCounter (a test checks they match). It is written here as a
# fixed string because SQL must never be built from variables.
TABLE = "core_ratelimitcounter"

_UPSERT = """
    INSERT INTO core_ratelimitcounter (key, window_start, count)
    VALUES (%s, %s, 1)
    ON CONFLICT (key, window_start) DO UPDATE SET count = core_ratelimitcounter.count + 1
    RETURNING count
"""


@dataclass(frozen=True)
class Limit:
    """At most `max_hits` uses per `window`, counted separately for each key."""

    name: str
    max_hits: int
    window: timedelta


# The limits for sign-in, staff login and setup.
STAFF_LOGIN = Limit("staff-login", max_hits=10, window=timedelta(minutes=15))
GOOGLE_START = Limit("google-start", max_hits=20, window=timedelta(minutes=15))
FIRST_SIGN_IN = Limit("first-sign-in", max_hits=5, window=timedelta(hours=1))
# Per user: saving the profile settings.
PROFILE_SETTINGS = Limit("profile-settings", max_hits=20, window=timedelta(minutes=15))
# First-run setup form, per IP. The token itself is unguessable; this only slows down noise.
SETUP = Limit("setup", max_hits=10, window=timedelta(minutes=15))


def window_start(limit: Limit, now: datetime) -> datetime:
    """The start of the fixed window that `now` falls in (windows line up with the Unix epoch)."""
    seconds = int(limit.window.total_seconds())
    start = int(now.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(start, tz=UTC)


def hit(limit: Limit, key: str, now: datetime | None = None) -> bool:
    """Count one use of `key` under `limit`. Return True if it is allowed, False if over the limit.

    Refused attempts count too, so hammering doesn't reset anything.
    """
    # Hashed: keys may contain user input (a username), which then never reaches the database as
    # text, and every key fits the column.
    digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()
    stored_key = f"{limit.name}:{digest}"
    start = window_start(limit, now or timezone.now())
    with connection.cursor() as cursor:
        cursor.execute(_UPSERT, [stored_key, start])
        (count,) = cursor.fetchone()
    return bool(count <= limit.max_hits)


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: HttpRequest) -> str:
    """The address of the person making the request, as well as we can tell.

    Public role and the admin through the tunnel: Cloudflare puts the visitor's address in
    `Cf-Connecting-Ip`. Both roles: the edge
    puts the address it saw as the last `X-Forwarded-For` entry; earlier entries came from the
    client and are ignored. Failing those, the TCP peer. The headers count only when the TCP peer
    is the edge (settings.GRADER_EDGE_ADDRESSES): anyone else could write them.
    """
    peer = str(request.META.get("REMOTE_ADDR", ""))
    if peer not in settings.GRADER_EDGE_ADDRESSES:
        return peer
    through_cloudflare = settings.GRADER_ROLE == "public" or (
        request.headers.get("X-Edge-Zone") == "admin-remote"  # the admin via the tunnel (:8082)
    )
    if through_cloudflare:
        cloudflare = _valid_ip(request.headers.get("Cf-Connecting-Ip", ""))
        if cloudflare:
            return cloudflare
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        last = _valid_ip(forwarded.split(",")[-1])
        if last:
            return last
    return peer
