"""URLs of the admin role: the Django admin (and later the admin pages and the judge API)."""

from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path

from accounts.views import logout_everywhere
from core.views import healthz

urlpatterns: list[URLPattern | URLResolver] = [
    path("healthz", healthz, name="healthz"),
    # Django's language switch (set_language): POST with a CSRF token.
    path("i18n/", include("django.conf.urls.i18n")),
    path("admin/", admin.site.urls),
    path("account/logout-everywhere", logout_everywhere, name="logout-everywhere"),
]
