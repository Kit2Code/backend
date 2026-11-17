"""Every API error as the contract's `{"error": {"code", "message", "fields"?}}` (contract §12)."""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.views import defaults
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler

# Codes the contract names; an exception raised with one of these keeps it.
CONTRACT_CODES = {
    "validation_error",
    "no_pending_signin",
    "not_authenticated",
    "csrf_failed",
    "registration_closed",
    "not_found",
    "method_not_allowed",
    "payload_too_large",
    "unsupported_media_type",
    "cooldown",
    "too_many_waiting",
    "hourly_limit",
    "rate_limited",
    "server_error",
}
# DRF's own codes that the contract spells differently (parse_error, invalid, throttled, ...).
BY_STATUS = {400: "validation_error", 401: "not_authenticated", 429: "rate_limited"}


def body(code: str, message: str, fields: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return {"error": error}


def _first_message(detail: Any) -> str:
    if isinstance(detail, dict):
        return _first_message(next(iter(detail.values()), ""))
    if isinstance(detail, list):
        return _first_message(detail[0]) if detail else ""
    return str(detail)


def handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF's EXCEPTION_HANDLER. Anything DRF doesn't know stays a 500 (handler500 below)."""
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()
    response = exception_handler(exc, context)  # sets Retry-After, WWW-Authenticate, rollback
    if response is None or not isinstance(exc, exceptions.APIException):
        return response
    own = exc.get_codes()
    if isinstance(own, str) and own in CONTRACT_CODES:
        code = own
    else:
        code = BY_STATUS.get(response.status_code, own if isinstance(own, str) else "error")
    fields = response.data if code == "validation_error" and isinstance(exc.detail, dict) else None
    response.data = body(code, _first_message(exc.detail), fields)
    return response


def _is_api(request: HttpRequest) -> bool:
    return request.path.startswith("/api/")


def not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """handler404: JSON under /api/ (unknown API URLs), the old HTML page elsewhere."""
    if _is_api(request):
        return JsonResponse(body("not_found", "Not found."), status=404)
    return defaults.page_not_found(request, exception)


def server_error(request: HttpRequest) -> HttpResponse:
    if _is_api(request):
        return JsonResponse(body("server_error", "Something went wrong."), status=500)
    return defaults.server_error(request)
