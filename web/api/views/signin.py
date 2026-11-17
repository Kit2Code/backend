"""Sign-in, registration, logout and settings for the SPA (contract §6, PLAN.md §7.1).

Google sign-in is a browser redirect: `start` and `callback` answer 302s, never JSON. They end at
the SPA's `next` path, `/welcome` (new account) or `/signin-error?code=…`.
"""

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import logout
from django.db.models import F
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import (
    APIException,
    NotAuthenticated,
    Throttled,
    ValidationError,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import avatars, google
from accounts.forms import FirstSignInForm, ProfileSettingsForm
from accounts.models import User
from accounts.views import create_account, find_identity, log_in
from api import schema
from api.serializers.users import UserSerializer
from api.views.problems import signed_in_user
from core import ratelimit
from core.models import SiteSetting

logger = logging.getLogger(__name__)


def _signin_error(code: str) -> HttpResponse:
    return HttpResponseRedirect(f"/signin-error?code={code}")


@require_safe
@never_cache
def google_start(request: HttpRequest) -> HttpResponse:
    config = google.load_config()
    if config is None:
        return _signin_error("unavailable")
    if not ratelimit.hit(ratelimit.GOOGLE_START, ratelimit.client_ip(request)):
        return _signin_error("rate_limited")
    url = google.begin(
        config, request.session, request.GET.get("next"), settings.GOOGLE_API_REDIRECT_URI
    )
    return HttpResponseRedirect(url)


@require_safe
@never_cache
def google_callback(request: HttpRequest) -> HttpResponse:
    config = google.load_config()
    if config is None:
        return _signin_error("unavailable")
    try:
        account, next_url = google.finish(config, request.session, request.GET.dict())
    except google.SignInError as error:
        logger.info("Google sign-in failed: %s", error)
        return _signin_error("failed")
    identity = find_identity(account)
    if identity is not None:
        if not identity.user.is_active:
            return _signin_error("account_disabled")
        log_in(request, identity, account.picture)
        return HttpResponseRedirect(next_url)  # google.safe_next() checked it
    if not SiteSetting.get().registration_open:
        return _signin_error("registration_closed")
    google.remember_pending(request.session, account, next_url)
    return HttpResponseRedirect("/welcome")


class _Coded(APIException):
    """An error with a contract code of its own (contract §12)."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        super().__init__(detail, code)


def _form_data(data: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError({"non_field_errors": ["Send a JSON object."]})
    return {name: data[name] for name in fields if name in data}


class RegisterView(APIView):
    @extend_schema(
        operation_id="auth_register", request=schema.Register, responses={201: schema.UserEnvelope}
    )
    def post(self, request: Request) -> Response:
        pending = google.pending_account(request.session)
        if pending is None:
            raise _Coded(400, "no_pending_signin", "There is no Google sign-in to finish.")
        account, _next_url = pending
        if not SiteSetting.get().registration_open:
            raise _Coded(403, "registration_closed", "New accounts are closed.")
        if not ratelimit.hit(ratelimit.FIRST_SIGN_IN, ratelimit.client_ip(request._request)):
            raise Throttled
        fields = ("username", "display_name", "accept_privacy")
        form = FirstSignInForm(_form_data(request.data, fields))
        if not form.is_valid():
            raise ValidationError(form.errors)
        identity = find_identity(account) or create_account(
            form.cleaned_data["username"], form.cleaned_data["display_name"], account
        )
        if identity is None:  # the username was taken between the check and the insert
            raise ValidationError({"username": [_("That username is taken.")]})
        if not identity.user.is_active:
            raise _Coded(403, "account_disabled", "This account has been disabled.")
        del request.session[google.PENDING_SESSION_KEY]
        log_in(request._request, identity, account.picture)
        return Response({"user": UserSerializer(identity.user).data}, status=201)


class LogoutView(APIView):
    @extend_schema(operation_id="auth_logout", request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        logout(request._request)
        return Response(status=204)


class LogoutEverywhereView(APIView):
    @extend_schema(operation_id="auth_logout_everywhere", request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        """Raising `session_version` ends every other session too (accounts.models.User)."""
        user = signed_in_user(request)
        if user is not None:
            User.objects.filter(pk=user.pk).update(session_version=F("session_version") + 1)
        logout(request._request)
        return Response(status=204)


class MyAccountView(APIView):
    @extend_schema(operation_id="me_update", request=schema.MyAccount, responses=UserSerializer)
    def patch(self, request: Request) -> Response:
        user = signed_in_user(request)
        if user is None:
            raise NotAuthenticated
        if not ratelimit.hit(ratelimit.PROFILE_SETTINGS, f"user:{user.pk}"):
            raise Throttled
        current = {"display_name": user.display_name, "show_photo": user.show_photo}
        changes = _form_data(request.data, ("display_name", "show_photo"))
        form = ProfileSettingsForm({**current, **changes}, instance=user)
        if not form.is_valid():
            raise ValidationError(form.errors)
        form.save()
        if not user.show_photo:
            avatars.forget(user)
        user.refresh_from_db()
        return Response(UserSerializer(user).data)
