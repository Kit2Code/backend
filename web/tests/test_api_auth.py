"""CSRF-strict session auth (PLAN.md §5.4) and `GET /api/auth/me` (contract §6)."""

from datetime import UTC, datetime

import pytest
from django.urls import URLPattern, include, path
from pytest_django.fixtures import Settings as SettingsWrapper
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

import api.urls
from accounts.models import Avatar, User
from accounts.views import photo
from api.auth import StrictSessionAuthentication
from judging.views import submission_source
from problems.views import problem_file
from tests.conftest import EdgeClient, use_role

pytestmark = pytest.mark.django_db


class EchoView(APIView):
    """A test-only endpoint with an unsafe method, and one that needs a signed-in user."""

    def post(self, request: Request) -> Response:
        return Response({"ok": True})


class PrivateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({"ok": True})


handler404 = "api.errors.not_found"
urlpatterns = [
    path("api/", include("api.urls")),
    path("api/test/echo", EchoView.as_view()),
    path("api/test/private", PrivateView.as_view()),
]


@pytest.fixture(autouse=True)
def public_with_test_views(settings: SettingsWrapper) -> None:
    use_role(settings, "public", urls=__name__)


def alice() -> User:
    return User.objects.create_user(username="alice", display_name="Alice A.")


def test_me_signed_out_sets_csrf_cookie() -> None:
    client = EdgeClient()
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"] is None
    assert body["csrf_token"]
    assert "g_csrf" in response.cookies


def test_me_signed_in() -> None:
    client = EdgeClient()
    client.force_login(alice())
    assert client.get("/api/auth/me").json()["user"] == {
        "username": "alice",
        "display_name": "Alice A.",
        "show_photo": True,
        "photo_url": None,
    }


def test_me_photo_url_and_photo() -> None:
    user = alice()
    Avatar.objects.create(user=user, image=b"\x89PNG", content_type="image/png")
    when = datetime(2026, 10, 26, tzinfo=UTC)
    User.objects.filter(pk=user.pk).update(photo_updated_at=when)
    client = EdgeClient()
    client.force_login(user)
    url = client.get("/api/auth/me").json()["user"]["photo_url"]
    assert url == f"/api/users/alice/photo?v={int(when.timestamp())}"
    response = client.get(url)
    assert (response.status_code, response.content) == (200, b"\x89PNG")


def test_missing_photo_is_json_404() -> None:
    response = EdgeClient().get("/api/users/nobody/photo")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unsafe_request_without_token_is_csrf_failed_signed_out() -> None:
    response = EdgeClient(enforce_csrf_checks=True).post("/api/test/echo", {}, "application/json")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_unsafe_request_without_token_is_csrf_failed_signed_in() -> None:
    client = EdgeClient(enforce_csrf_checks=True)
    client.force_login(alice())
    response = client.post("/api/test/echo", {}, "application/json")
    assert response.json()["error"]["code"] == "csrf_failed"


def test_unsafe_request_with_token_from_me_passes() -> None:
    client = EdgeClient(enforce_csrf_checks=True)
    token = client.get("/api/auth/me").json()["csrf_token"]
    response = client.post("/api/test/echo", {}, "application/json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200


def test_signed_out_on_protected_endpoint_is_401() -> None:
    response = EdgeClient().get("/api/test/private")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_inactive_user_counts_as_signed_out() -> None:
    user = alice()
    client = EdgeClient()
    client.force_login(user)
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert client.get("/api/test/private").status_code == 401


# Function views allowed in the API: each must answer safe methods only.
SAFE_FUNCTION_VIEWS = {photo, problem_file, submission_source}


def test_every_api_view_uses_strict_session_auth() -> None:
    """An empty or stock `authentication_classes` on any view would silently drop CSRF."""
    for pattern in api.urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        view_class = getattr(pattern.callback, "cls", None)
        if view_class is None:
            assert pattern.callback in SAFE_FUNCTION_VIEWS, pattern
            continue
        assert issubclass(view_class, APIView), pattern
        assert list(view_class.authentication_classes) == [StrictSessionAuthentication], pattern
