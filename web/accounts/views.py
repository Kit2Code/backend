"""Signing in with Google (public role) and signing out (both roles)."""

import logging

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST, require_safe

from accounts import avatars, google
from accounts.forms import FirstSignInForm, ProfileSettingsForm
from accounts.models import Avatar, GoogleIdentity, User
from core import ratelimit
from core.models import SiteSetting
from core.views import message_page, too_many_requests

logger = logging.getLogger(__name__)


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Sign out of this browser. POST only, so another site can't sign people out with a link."""
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


@require_POST
def logout_everywhere(request: HttpRequest) -> HttpResponse:
    """Sign out of every browser and device at once.

    Raising `session_version` changes the user's session hash, so Django rejects all their other
    sessions on their next request (see accounts.models.User).
    """
    if request.user.is_authenticated:
        User.objects.filter(pk=request.user.pk).update(session_version=F("session_version") + 1)
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


# ---- Google sign-in (public role only) ----


def _sign_in_unavailable(request: HttpRequest) -> HttpResponse:
    return message_page(
        request,
        _("Sign-in is not set up yet"),
        _("You can still read the problems. Sign-in will be available soon."),
        marker="signin-unavailable",
    )


def _sign_in_failed(request: HttpRequest) -> HttpResponse:
    return message_page(
        request,
        _("Sign-in failed"),
        _("Signing in with Google didn't work. Please try again."),
        status=400,
        link_url=reverse("google-start"),
        link_text=_("Try again"),
    )


def _registration_closed(request: HttpRequest) -> HttpResponse:
    return message_page(
        request,
        _("New accounts are closed"),
        _("This site isn't accepting new accounts right now."),
        status=403,
    )


def _sign_in_member(
    request: HttpRequest, identity: GoogleIdentity, next_url: str, picture: str
) -> HttpResponse:
    """Log in the user who owns `identity`, unless an admin has disabled them, and refresh their
    Google photo from the `picture` address."""
    if not identity.user.is_active:
        return message_page(
            request,
            _("Account disabled"),
            _("This account has been disabled. Ask the person who runs this site."),
            status=403,
        )
    # login() gives the session a new key, so a session id seen before sign-in is worthless.
    login(request, identity.user, backend="django.contrib.auth.backends.ModelBackend")
    GoogleIdentity.objects.filter(pk=identity.pk).update(last_login_at=timezone.now())
    avatars.refresh(identity.user, picture)
    return redirect(next_url)


def _find_identity(account: google.GoogleAccount) -> GoogleIdentity | None:
    return (
        GoogleIdentity.objects.select_related("user")
        .filter(issuer=account.issuer, subject=account.subject)
        .first()
    )


def google_start(request: HttpRequest) -> HttpResponse:
    """Send the browser to Google's sign-in page."""
    config = google.load_config()
    if config is None:
        return _sign_in_unavailable(request)
    if not ratelimit.hit(ratelimit.GOOGLE_START, ratelimit.client_ip(request)):
        return too_many_requests(request)
    return redirect(google.begin(config, request.session, request.GET.get("next")))


def google_callback(request: HttpRequest) -> HttpResponse:
    """Google sends the browser back here after sign-in."""
    config = google.load_config()
    if config is None:
        return _sign_in_unavailable(request)
    try:
        account, next_url = google.finish(config, request.session, request.GET.dict())
    except google.SignInError as error:
        logger.info("Google sign-in failed: %s", error)
        return _sign_in_failed(request)
    identity = _find_identity(account)
    if identity is not None:
        return _sign_in_member(request, identity, next_url, account.picture)
    if not SiteSetting.get().registration_open:
        return _registration_closed(request)
    google.remember_pending(request.session, account, next_url)
    return redirect("welcome")


def welcome(request: HttpRequest) -> HttpResponse:
    """The first sign-in form: a new Google account picks its username and display name."""
    pending = google.pending_account(request.session)
    if pending is None:
        return redirect("google-start")
    account, next_url = pending
    if not SiteSetting.get().registration_open:
        return _registration_closed(request)
    if request.method != "POST":
        return render(request, "accounts/welcome.html", {"form": FirstSignInForm()})

    if not ratelimit.hit(ratelimit.FIRST_SIGN_IN, ratelimit.client_ip(request)):
        return too_many_requests(request)
    form = FirstSignInForm(request.POST)
    if form.is_valid():
        identity = _find_identity(account)
        if identity is None:
            identity = _create_account(form, account)
        if identity is not None:
            del request.session[google.PENDING_SESSION_KEY]
            return _sign_in_member(request, identity, next_url, account.picture)
        form.add_error("username", _("That username is taken."))
    return render(request, "accounts/welcome.html", {"form": form})


def _create_account(form: FirstSignInForm, account: google.GoogleAccount) -> GoogleIdentity | None:
    """Create the user and link the Google account, or return None if the username got taken.

    If the same Google account was linked in the meantime (another tab), return that link.
    """
    try:
        with transaction.atomic():
            user = User(
                username=form.cleaned_data["username"],
                display_name=form.cleaned_data["display_name"],
            )
            user.set_unusable_password()
            user.save()
            return GoogleIdentity.objects.create(
                user=user, issuer=account.issuer, subject=account.subject
            )
    except IntegrityError:
        return _find_identity(account)


# ---- profile photo and settings (public role) ----


@require_safe
@cache_control(public=True, max_age=3600)
def photo(request: HttpRequest, username: str) -> HttpResponse:
    """A user's stored Google photo. Pages add `?v=<when it changed>`, so caching is safe."""
    avatar = (
        Avatar.objects.filter(user__username=username, user__is_active=True, user__show_photo=True)
        .only("image", "content_type")
        .first()
    )
    if avatar is None:
        raise Http404
    return HttpResponse(bytes(avatar.image), content_type=avatar.content_type)


@login_required
def profile_settings(request: HttpRequest) -> HttpResponse:
    """The signed-in user's display name and photo choice. POST with CSRF, rate-limited."""
    user = request.user
    if not isinstance(user, User):  # login_required already sent anonymous visitors away
        return redirect("home")
    if request.method != "POST":
        return render(
            request, "accounts/settings.html", {"form": ProfileSettingsForm(instance=user)}
        )
    if not ratelimit.hit(ratelimit.PROFILE_SETTINGS, f"user:{user.pk}"):
        return too_many_requests(request)
    form = ProfileSettingsForm(request.POST, instance=user)
    if not form.is_valid():
        return render(request, "accounts/settings.html", {"form": form})
    form.save()
    if not user.show_photo:
        avatars.forget(user)
    return redirect("profile", username=user.username)
