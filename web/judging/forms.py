"""The submit form."""

from collections.abc import Sequence
from typing import Any, cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from grader_common.problem_format import LANGUAGE_NAMES
from judging.models import MAX_SOURCE_BYTES, Verdict


class SubmitForm(forms.Form):
    """Language plus source code, typed into the box or uploaded as a file (the file wins)."""

    language = forms.ChoiceField(label=_("Language"))
    source = forms.CharField(
        label=_("Source code"),
        required=False,
        strip=False,  # keep the code exactly as written
        widget=forms.Textarea(attrs={"rows": 18, "spellcheck": "false", "autocomplete": "off"}),
    )
    file = forms.FileField(label=_("…or upload a file"), required=False)

    def __init__(self, *args: Any, languages: Sequence[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = cast(forms.ChoiceField, self.fields["language"])
        field.choices = [(code, LANGUAGE_NAMES[code]) for code in languages]

    def clean_file(self) -> str | None:
        """The uploaded file as text, or None when there is no file."""
        upload: UploadedFile[bytes] | None = self.cleaned_data.get("file")
        if upload is None:
            return None
        content: bytes = upload.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise forms.ValidationError(_("The file is bigger than 64 KB."))
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            raise forms.ValidationError(_("The file must be UTF-8 text.")) from None

    def clean(self) -> dict[str, Any]:
        """Pick the code (file first, then the box) and check it once, whichever it came from."""
        cleaned = super().clean() or {}
        code: str = cleaned.get("file") or cleaned.get("source") or ""
        if not code.strip():
            raise forms.ValidationError(_("Please type or upload your code."))
        if len(code.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise forms.ValidationError(_("The code is longer than 64 KB."))
        if "\x00" in code:  # Postgres text can't hold NUL, and no real program needs one
            raise forms.ValidationError(_("The code contains a NUL character."))
        cleaned["code"] = code
        return cleaned


class SubmissionFilterForm(forms.Form):
    """The filters above the list of all submissions (all optional, read from the query string)."""

    problem = forms.SlugField(label=_("Problem"), required=False, max_length=64)
    user = forms.CharField(label=_("User"), required=False, max_length=20)
    verdict = forms.ChoiceField(
        label=_("Result"), required=False, choices=[("", "—"), *Verdict.choices]
    )
