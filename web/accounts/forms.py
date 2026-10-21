"""Forms for accounts."""

from django import forms
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
