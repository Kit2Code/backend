"""User accounts and the Google identities linked to them."""

from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

USERNAME_RULE = RegexValidator(
    r"^[a-z0-9_]{3,20}\Z",
    _("3 to 20 characters: small letters a-z, digits and _ only."),
)


class User(AbstractUser):
    """A site user.

    Students sign in with Google on the public site and have no usable password. Staff sign in with
    a username and password on the admin site only.
    """

    username = models.CharField(
        _("username"),
        max_length=20,
        unique=True,
        validators=[USERNAME_RULE],
        help_text=_("3 to 20 characters: small letters a-z, digits and _ only."),
        error_messages={"unique": _("That username is taken.")},
    )
    display_name = models.CharField(_("display name"), max_length=100, blank=True)
    school = models.CharField(_("school"), max_length=100, blank=True)
    # Raised by "log out everywhere": every existing session of this user stops being valid.
    session_version = models.PositiveIntegerField(default=0, editable=False)

    def _get_session_auth_hash(self, secret: str | None = None) -> str:
        """Django's session hash, plus `session_version`.

        Django stores this hash in each session and logs the session out when it no longer
        matches (normally after a password change). Including `session_version` means one counter
        update ends all sessions, even for Google users who have no password to change.
        """
        key_salt = "accounts.User.get_session_auth_hash"
        return salted_hmac(
            key_salt,
            f"{self.password}:{self.session_version}",
            secret=secret,
            algorithm="sha256",
        ).hexdigest()


class GoogleIdentity(models.Model):
    """The Google account a user signs in with: only Google's issuer and account id, no more."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="google_identity")
    issuer = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Google identity")
        verbose_name_plural = _("Google identities")
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "subject"], name="googleidentity_iss_sub_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} ({self.issuer})"
