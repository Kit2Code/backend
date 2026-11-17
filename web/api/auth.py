"""Session auth for the API (PLAN.md §5.4). Stock DRF SessionAuthentication checks CSRF only for
signed-in users and answers "not signed in" with 403; the contract wants CSRF on every unsafe
request (`403 csrf_failed`) and `401 not_authenticated`."""

from typing import Any

from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request


class CsrfFailed(PermissionDenied):
    default_code = "csrf_failed"


class StrictSessionAuthentication(SessionAuthentication):
    def authenticate(self, request: Request) -> tuple[Any, None] | None:
        if request.method not in SAFE_METHODS:
            try:
                self.enforce_csrf(request)  # signed in or not
            except PermissionDenied as exc:
                # A plain CsrfFailed(exc.detail) would keep the code 'permission_denied'.
                raise CsrfFailed(str(exc.detail), code="csrf_failed") from exc
        user = getattr(request._request, "user", None)
        if user is None or not user.is_active:
            return None
        return (user, None)

    def authenticate_header(self, request: Request) -> str:
        return "Session"  # any value makes DRF answer NotAuthenticated with 401
