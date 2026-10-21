"""The zone check and role-bound sessions: the second layer of the two-role design.

See docs/ARCHITECTURE.md, "One web image, two roles". The edge is the first layer: it routes each
port to one role, adds `X-Edge-Zone`, and strips any copy a client sent. These middlewares make
Django refuse anything that doesn't fit, even if the edge were misconfigured or bypassed.
"""

import hmac
from collections.abc import Callable

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.http import Http404, HttpRequest, HttpResponseBase

# Paths that work without a zone header: Docker's healthcheck calls them inside the container.
ZONE_EXEMPT_PATHS = frozenset({"/healthz"})

SESSION_ROLE_KEY = "_role"

GetResponse = Callable[[HttpRequest], HttpResponseBase]


def is_internal_path(path: str) -> bool:
    """True for the judge API, /internal and everything below it."""
    return path == "/internal" or path.startswith("/internal/")


def has_worker_token(request: HttpRequest) -> bool:
    """True if the request carries `Authorization: Bearer <worker token>`, exactly."""
    expected = settings.WORKER_TOKEN
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not expected or not header.startswith(prefix):
        return False
    # compare_digest takes the same time however many characters match, so the response time
    # doesn't help anyone guess the token one character at a time.
    return hmac.compare_digest(header[len(prefix) :].encode(), expected.encode())


def zone_allows(request: HttpRequest) -> bool:
    """Decide whether this request may reach the role's URLs at all."""
    role = settings.GRADER_ROLE
    zone = request.headers.get("X-Edge-Zone")
    path = request.path_info
    if role == "admin" and is_internal_path(path):
        # Only the worker, directly over judge_net: never through the edge, always with the token.
        return zone is None and has_worker_token(request)
    if path in ZONE_EXEMPT_PATHS:
        return True
    return zone == role


class EdgeZoneMiddleware:
    """Answer 404 to any request whose zone doesn't match this role.

    404 rather than 403, so a scanner can't even tell that something exists here.
    """

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if not zone_allows(request):
            raise Http404
        return self.get_response(request)


class RoleSessionMiddleware:
    """Tie each session to the role that created it, and drop it if it shows up in the other role.

    Each role already uses its own cookie name, so a browser never mixes them up by itself. This
    catches a session key that was copied on purpose: a stolen public cookie must never work on
    the admin site (or the other way round). Sessions with no role mark are dropped too.
    """

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        session = request.session
        if not session.is_empty() and session.get(SESSION_ROLE_KEY) != settings.GRADER_ROLE:
            session.flush()
        response = self.get_response(request)
        # A session the view just started (for example the sign-in state) gets this role's mark.
        if not session.is_empty() and SESSION_ROLE_KEY not in session:
            session[SESSION_ROLE_KEY] = settings.GRADER_ROLE
        return response


@receiver(user_logged_in)
def mark_session_role_at_login(request: HttpRequest | None, **kwargs: object) -> None:
    """Mark the session with the role at every login (login() replaces the session key)."""
    if request is not None:
        request.session[SESSION_ROLE_KEY] = settings.GRADER_ROLE


class ThaiByDefaultMiddleware:
    """Ignore the browser's preferred language, so the site is Thai until the visitor picks English.

    Django's LocaleMiddleware (next in the list) would otherwise follow `Accept-Language`, and
    many Thai students' browsers ask for English. A choice made with the language switch is kept
    in a cookie, which LocaleMiddleware still honours.
    """

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        request.META.pop("HTTP_ACCEPT_LANGUAGE", None)
        return self.get_response(request)
