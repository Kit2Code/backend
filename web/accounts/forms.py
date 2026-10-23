"""Forms for accounts."""

from typing import Any

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.models import USERNAME_RULE, User
from core.validators import no_control_characters


class FirstSignInForm(forms.Form):
    """Shown once, after the first Google sign-in: the public profile for the new account."""

    username = forms.CharField(
        label=_("Username"),
        max_length=20,
        validators=[USERNAME_RULE],
        help_text=_("3 to 20 characters: small letters a-z, digits and _ only."),
    )
    display_name = forms.CharField(
        label=_("Display name"), max_length=100, validators=[no_control_characters]
    )
    school = forms.CharField(
        label=_("School (optional)"),
        max_length=100,
        required=False,
        validators=[no_control_characters],
    )
    accept_privacy = forms.BooleanField(label=_("I have read the privacy page"))

    def clean_username(self) -> str:
        username: str = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError(_("That username is taken."))
        return username


class ProfileSettingsForm(forms.ModelForm):  # type: ignore[type-arg]
    """The signed-in user's own settings: how others see them, and whether to show their photo."""

    class Meta:
        model = User
        fields = ["display_name", "school", "show_photo"]
        labels = {
            "display_name": _("Display name"),
            "school": _("School (optional)"),
            "show_photo": _("Show my Google profile photo"),
        }
        help_texts = {
            "show_photo": _(
                "Switching this off deletes the photo we keep. Switched on, it is fetched again "
                "the next time you sign in."
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["display_name"].required = True
        for name in ("display_name", "school"):
            self.fields[name].validators.append(no_control_characters)


class FirstSuperuserForm(forms.Form):
    """First-run setup: the first admin account's username and password."""

    username = forms.CharField(
        label=_("Username"),
        max_length=20,
        validators=[USERNAME_RULE],
        help_text=_("3 to 20 characters: small letters a-z, digits and _ only."),
    )
    password1 = forms.CharField(label=_("Password"), widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label=_("Password again"), widget=forms.PasswordInput, strip=False)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        first, second = cleaned.get("password1"), cleaned.get("password2")
        if first and second:
            if first != second:
                self.add_error("password2", _("The two passwords are different."))
            else:
                try:
                    password_validation.validate_password(
                        first, User(username=cleaned.get("username", ""))
                    )
                except ValidationError as error:
                    self.add_error("password1", error)
        return cleaned
