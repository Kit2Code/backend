"""A test-only URLconf: one page that says who the request is signed in as."""

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, path


def whoami(request: HttpRequest) -> HttpResponse:
    """Reply with the signed-in username (or "anonymous"), and put something in the session."""
    request.session["seen"] = True
    name = request.user.get_username() if request.user.is_authenticated else "anonymous"
    return HttpResponse(name, content_type="text/plain")


def internal_ping(request: HttpRequest) -> HttpResponse:
    return HttpResponse("pong", content_type="text/plain")


urlpatterns: list[URLPattern] = [
    path("whoami", whoami),
    path("internal/ping", internal_ping),
]
