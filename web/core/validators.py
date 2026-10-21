"""Validators shared by several models."""

import unicodedata

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _


@deconstructible
class MaxUtf8BytesValidator:
    """Refuse text longer than `limit` bytes once encoded as UTF-8.

    Django's `max_length` counts characters, but a Thai letter is 3 bytes in UTF-8, so a byte cap
    needs its own check. `@deconstructible` lets migrations record the validator and its limit.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def __call__(self, value: str) -> None:
        size = len(value.encode("utf-8"))
        if size > self.limit:
            raise ValidationError(
                _("At most %(limit)d bytes (this is %(size)d)."),
                code="too_many_bytes",
                params={"limit": self.limit, "size": size},
            )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MaxUtf8BytesValidator) and other.limit == self.limit

    def __hash__(self) -> int:
        return hash(self.limit)


def no_control_characters(value: str) -> None:
    """Refuse control and invisible formatting characters (for names shown to other people).

    Unicode categories Cc (control, e.g. NUL) and Cf (format, e.g. the right-to-left override
    that can make a name display backwards). Thai combining vowels and tone marks are Mn, so they
    are allowed.
    """
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value):
        raise ValidationError(_("Control or invisible characters are not allowed."), code="control")
