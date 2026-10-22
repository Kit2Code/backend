"""Small views and responses shared by both roles."""

from django.contrib import admin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

from accounts.forms import FirstSuperuserForm
from core import audit, ratelimit, setup
from core.models import SiteSetting
from problems.views import recent_problems


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


SETUP_SESSION_KEY = "setup_token_hash"


@never_cache
def setup_view(request: HttpRequest) -> HttpResponse:
    """First-run setup (admin role): create the first superuser with the one-time link.

    `/setup?token=…` keeps the token's hash in the session (never the token: sessions live in the
    database) and redirects to plain `/setup`, so the token doesn't stay in the address bar or
    later log lines. Anything unusable is a 404.
    """
    token = request.GET.get("token")
    if request.method == "GET" and token is not None:
        token_hash = setup.hash_token(token)
        if setup.usable_token(token_hash) is None:
            raise Http404
        request.session[SETUP_SESSION_KEY] = token_hash
        return redirect("setup")
    token_hash = request.session.get(SETUP_SESSION_KEY, "")
    if setup.usable_token(token_hash) is None:
        raise Http404
    if request.method != "POST":
        return _setup_page(request, FirstSuperuserForm())
    if not ratelimit.hit(ratelimit.SETUP, ratelimit.client_ip(request)):
        return too_many_requests(request)
    form = FirstSuperuserForm(request.POST)
    if not form.is_valid():
        return _setup_page(request, form)
    user = setup.create_first_superuser(
        token_hash, form.cleaned_data["username"], form.cleaned_data["password1"]
    )
    if user is None:
        raise Http404
    del request.session[SETUP_SESSION_KEY]
    audit.record(request, "setup.superuser_created", user, actor=user)
    return redirect("admin:login")


def _setup_page(request: HttpRequest, form: FirstSuperuserForm) -> HttpResponse:
    context = {**admin.site.each_context(request), "form": form}
    return render(request, "admin/grader/setup.html", context)
