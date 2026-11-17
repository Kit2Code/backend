"""The user object (contract §6, "User object")."""

from django.urls import reverse
from rest_framework import serializers

from accounts.models import User


class UserSerializer(serializers.Serializer[User]):
    username = serializers.CharField()
    display_name = serializers.CharField()
    show_photo = serializers.BooleanField()
    photo_url = serializers.SerializerMethodField()

    def get_photo_url(self, user: User) -> str | None:
        if user.photo_updated_at is None or not user.show_photo:
            return None
        url = reverse("api-user-photo", args=[user.username])
        return f"{url}?v={int(user.photo_updated_at.timestamp())}"
