"""Writing the audit log: who did what, to what, when and from where."""

from typing import Any

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.http import HttpRequest

from core.models import AuditLog
from core.ratelimit import client_ip


def record(
    request: HttpRequest,
    action: str,
    target: object = "",
    details: dict[str, Any] | None = None,
    *,
    actor: Any = None,
) -> AuditLog:
    """Add one audit row for `request`. The actor is the signed-in user unless `actor` is given.

    `action` is a short dotted name such as "setup.superuser_created"; `target` names the thing
    it was done to (its str() is stored, cut to fit). `details` must never hold secrets.
    """
    user = actor if actor is not None else getattr(request, "user", None)
    if user is not None and not user.is_authenticated:
        user = None
    return AuditLog.objects.create(
        actor=user,
        actor_username=user.get_username() if user is not None else "",
        action=action,
        target=str(target)[:255],
        details=details or {},
        ip=client_ip(request) or None,
    )


# ---- staff sign-in (admin role only: public sign-in is Google's business) ----


@receiver(user_logged_in)
def _logged_in(sender: object, request: HttpRequest | None, user: Any, **kwargs: object) -> None:
    if request is not None and settings.GRADER_ROLE == "admin":
        record(request, "auth.login", user, actor=user)


@receiver(user_logged_out)
def _logged_out(sender: object, request: HttpRequest | None, user: Any, **kwargs: object) -> None:
    if request is not None and user is not None and settings.GRADER_ROLE == "admin":
        record(request, "auth.logout", user, actor=user)


@receiver(user_login_failed)
def _login_failed(
    sender: object,
    credentials: dict[str, Any],
    request: HttpRequest | None = None,
    **kwargs: object,
) -> None:
    if request is not None and settings.GRADER_ROLE == "admin":
        # Only the username: Django masks the password here, but there is no reason to keep it.
        username = str(credentials.get("username", ""))[:150]
        record(request, "auth.login_failed", username, {"username": username})
