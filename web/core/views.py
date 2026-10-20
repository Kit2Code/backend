"""Small views shared by both roles."""

from django.http import HttpRequest, HttpResponse


def healthz(request: HttpRequest) -> HttpResponse:
    """Liveness check for Docker healthchecks and the smoke test: the process answers."""
    return HttpResponse("ok", content_type="text/plain")
