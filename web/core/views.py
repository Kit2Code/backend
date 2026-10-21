"""Small views and responses shared by both roles."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

from core.models import SiteSetting
from problems.views import recent_problems


def healthz(request: HttpRequest) -> HttpResponse:
    """Liveness check for Docker healthchecks and the smoke test: the process answers."""
    return HttpResponse("ok", content_type="text/plain")


def home(request: HttpRequest) -> HttpResponse:
    """The public home page: the announcement and a way in to the problems."""
    context = {
        "announcement": SiteSetting.get().announcement,
        "recent_problems": recent_problems(),
    }
    return render(request, "core/home.html", context)


def privacy(request: HttpRequest) -> HttpResponse:
    """What we store about people, and why."""
    return render(request, "core/privacy.html")


def message_page(
    request: HttpRequest,
    heading: str,
    message: str,
    *,
    status: int = 200,
    marker: str = "",
    link_url: str = "",
    link_text: str = "",
) -> HttpResponse:
    """A simple page with a heading, one message and an optional link."""
    context = {
        "heading": heading,
        "message": message,
        "marker": marker,
        "link_url": link_url,
        "link_text": link_text,
    }
    return render(request, "core/message.html", context, status=status)


def too_many_requests(request: HttpRequest) -> HttpResponse:
    return message_page(
        request,
        _("Too many tries"),
        _("Please wait a while and try again."),
        status=429,
    )
