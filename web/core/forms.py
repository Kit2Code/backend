"""Forms of the admin pages for site-wide settings."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import MAX_TIME_SCALE, MIN_TIME_SCALE, PauseWindow
from core.validators import no_control_characters

# The browser's own date-and-time picker; the value is local time (Asia/Bangkok).
LOCAL_DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class PauseWindowForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = PauseWindow
        fields = ("starts_at", "ends_at", "note")
        widgets = {"starts_at": LOCAL_DATETIME, "ends_at": LOCAL_DATETIME}

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", _("The end must be after the start."))
        return cleaned


class TimeScaleForm(forms.Form):
    # FloatField refuses NaN and infinity by itself.
    time_scale = forms.FloatField(
        label=_("Time scale"),
        min_value=MIN_TIME_SCALE,
        max_value=MAX_TIME_SCALE,
        help_text=_("Every time limit is multiplied by this (1.0 = the contest's limits)."),
    )


class GoogleClientForm(forms.Form):
    client_id = forms.CharField(
        label=_("Client ID"), max_length=255, validators=[no_control_characters]
    )
    # Write-only: never put back into the page (render_value=False), and blank keeps the old one.
    client_secret = forms.CharField(
        label=_("Client secret"),
        required=False,
        max_length=255,
        validators=[no_control_characters],
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text=_("Leave empty to keep the secret that is already saved."),
    )
