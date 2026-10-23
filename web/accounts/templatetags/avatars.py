"""`{% avatar username photo_updated_at show_photo %}`: the user's photo, or their initial."""

from datetime import datetime

from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag
def avatar(
    username: str, photo_updated_at: datetime | None = None, show_photo: bool = True
) -> SafeString:
    """An <img> of the stored photo (its URL changes when the photo does, so it can be cached), or
    a circle with the username's first letter. Decorative: the name is always shown next to it."""
    if photo_updated_at is not None and show_photo:
        url = reverse("photo", args=[username])
        return format_html(
            '<img class="avatar" src="{}?v={}" alt="">', url, int(photo_updated_at.timestamp())
        )
    return format_html('<span class="avatar" aria-hidden="true">{}</span>', username[:1].upper())
