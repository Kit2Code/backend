"""User accounts."""

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """A site user. Our own model from the first migration, so we can add fields later."""
