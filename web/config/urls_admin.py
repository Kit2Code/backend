"""URLs of the admin role: the Django admin (and later the admin pages and the judge API)."""

from django.contrib import admin
from django.urls import URLPattern, URLResolver, path

from core.views import healthz

urlpatterns: list[URLPattern | URLResolver] = [
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
]
