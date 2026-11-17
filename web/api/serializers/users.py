"""The user object (contract §6, "User object")."""

from datetime import datetime

from django.urls import reverse
from rest_framework import serializers

from accounts.models import User


def photo_url(username: str, photo_updated_at: datetime | None, show_photo: bool) -> str | None:
    """The photo's URL; `?v=` changes with the photo, so it can be cached (contract §10)."""
    if photo_updated_at is None or not show_photo:
        return None
    return f"{reverse('api-user-photo', args=[username])}?v={int(photo_updated_at.timestamp())}"


class UserSerializer(serializers.Serializer[User]):
    username = serializers.CharField()
    display_name = serializers.CharField()
    show_photo = serializers.BooleanField()
    photo_url = serializers.SerializerMethodField()

    def get_photo_url(self, user: User) -> str | None:
        return photo_url(user.username, user.photo_updated_at, user.show_photo)
