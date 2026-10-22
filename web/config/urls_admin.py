"""URLs of the admin role: the Django admin with our pages, first-run setup and the judge API."""

from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path

from accounts.views import logout_everywhere
from core.views import setup_view
from judging import internal

urlpatterns: list[URLPattern | URLResolver] = [
    # Django's language switch (set_language): POST with a CSRF token.
    path("i18n/", include("django.conf.urls.i18n")),
    path("admin/", admin.site.urls),
    path("setup", setup_view, name="setup"),
    path("account/logout-everywhere", logout_everywhere, name="logout-everywhere"),
    # The judge API: for the worker only (see core.middleware and judging.internal).
    path("internal/worker/hello", internal.hello),
    path("internal/judge/claim", internal.claim),
    path("internal/judge/heartbeat", internal.heartbeat),
    path("internal/judge/progress", internal.progress),
    path("internal/judge/complete", internal.complete),
    path("internal/judge/fail", internal.fail),
    path("internal/judge/report", internal.report),
]
