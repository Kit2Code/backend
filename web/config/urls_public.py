"""URLs of the public role (the practice site). Admin, setup and internal URLs never go here."""

from django.urls import URLPattern, path

from core.views import healthz

urlpatterns: list[URLPattern] = [
    path("healthz", healthz, name="healthz"),
]
