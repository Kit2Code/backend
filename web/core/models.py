"""Site-wide settings, grading pause windows, the audit log, rate-limit counters, backups, setup."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _


class SiteSetting(models.Model):
    """The one row of settings an admin can change at runtime. Always use `SiteSetting.get()`."""

    grading_paused = models.BooleanField(_("grading paused"), default=False)
    time_scale = models.FloatField(_("time scale"), default=1.0)
    registration_open = models.BooleanField(_("registration open"), default=True)
    announcement = models.TextField(_("announcement"), blank=True)
    google_client_id = models.CharField(_("Google client ID"), max_length=255, blank=True)
    # Fernet token (see core.crypto); never shown back in the UI.
    google_client_secret_encrypted = models.TextField(blank=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(pk=1), name="sitesetting_single_row")]

    def __str__(self) -> str:
        return "Site settings"

    @classmethod
    def get(cls) -> "SiteSetting":
        """Return the settings row, creating it with defaults the first time."""
        setting, _created = cls.objects.get_or_create(pk=1)
        return setting


class PauseWindow(models.Model):
    """A planned time range (for example a CMS contest) when no submissions are judged."""

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="pausewindow_ends_after_start"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.starts_at:%Y-%m-%d %H:%M} – {self.ends_at:%Y-%m-%d %H:%M}"


class AuditLog(models.Model):
    """Who did what, when, from where. Rows are only ever added."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    # Kept as text too, so the log still says who it was after the user is deleted.
    actor_username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_username} {self.action}"


class RateLimitCounter(models.Model):
    """How many times `key` was used in the fixed window starting at `window_start`.

    Written only by core.ratelimit, with one atomic upsert.
    """

    key = models.CharField(max_length=200)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "window_start"], name="ratelimit_key_window_uniq"
            ),
        ]
        indexes = [models.Index(fields=["window_start"], name="ratelimit_window_idx")]

    def __str__(self) -> str:
        return f"{self.key} @ {self.window_start}: {self.count}"


class BackupRun(models.Model):
    """A backup, either scheduled or requested from the admin site ("back up now")."""

    class Status(models.TextChoices):
        REQUESTED = "requested", _("requested")
        RUNNING = "running", _("running")
        DONE = "done", _("done")
        FAILED = "failed", _("failed")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    file_name = models.CharField(max_length=255, blank=True)
    message = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Backup {self.pk} ({self.status})"


class SetupToken(models.Model):
    """A one-time first-run setup link. Only the SHA-256 of the token is stored."""

    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Setup token {self.pk}"
