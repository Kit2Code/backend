"""Shared test setup for the web app.

In production every request reaches Django through the edge, which adds `X-Edge-Zone`. The
`client` fixture below does the same, so ordinary tests behave like real traffic. Tests about the
zone check itself build a plain `Client()` and choose the header themselves.
"""

from typing import TYPE_CHECKING, Any

import pytest
from django.conf import settings
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from config import env

if TYPE_CHECKING:
    # The test client's response type; it exists only in the type stubs (django-stubs).
    from django.test.client import _MonkeyPatchedWSGIResponse


class EdgeClient(Client):
    """A test client that adds the zone header of the role under test, like the edge does."""

    def request(self, **request: Any) -> "_MonkeyPatchedWSGIResponse":
        request.setdefault("HTTP_X_EDGE_ZONE", settings.GRADER_ROLE)
        return super().request(**request)


def use_role(settings: SettingsWrapper, role: str, urls: str | None = None) -> None:
    """Switch the running test to one role: its URLconf (or `urls`), cookie names and role name.

    Use `urls` instead of `@pytest.mark.urls` in tests that switch roles: the switch sets the
    URLconf too, and would undo the marker.
    """
    settings.GRADER_ROLE = role
    settings.ROOT_URLCONF = urls or f"config.urls_{role}"
    for name, value in env.role_cookie_names(role).items():
        setattr(settings, name, value)


@pytest.fixture
def client() -> Client:
    return EdgeClient()


@pytest.fixture
def public_role(settings: SettingsWrapper) -> None:
    use_role(settings, "public")


@pytest.fixture
def admin_role(settings: SettingsWrapper) -> None:
    use_role(settings, "admin")
