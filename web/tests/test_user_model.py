"""The custom user model must exist from the very first migration (Django can't switch later)."""

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import CreateModel

from accounts.models import User


def test_auth_user_model_is_ours() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert get_user_model() is User


def test_first_accounts_migration_creates_user() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    first = loader.graph.root_nodes("accounts")
    assert first == [("accounts", "0001_initial")]
    migration = loader.graph.nodes[first[0]]
    created = [op.name for op in migration.operations if isinstance(op, CreateModel)]
    assert "User" in created


@pytest.mark.django_db
def test_create_user_stores_a_hash_not_the_password() -> None:
    user = User.objects.create_user(username="alice", password="correct-horse-1")
    assert user.password != "correct-horse-1"
    assert user.check_password("correct-horse-1")
    assert not user.is_staff
