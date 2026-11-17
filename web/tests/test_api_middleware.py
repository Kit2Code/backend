"""ApiMiddleware: language, UTC times, body limit, no-store (PLAN.md §5.1, §5.4)."""

from datetime import UTC, datetime

import pytest
from django.urls import include, path
from django.utils import timezone, translation
from pytest_django.fixtures import Settings as SettingsWrapper
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from tests.conftest import EdgeClient, use_role

pytestmark = pytest.mark.django_db

WHEN = datetime(2026, 10, 26, 9, 58, tzinfo=UTC)


class StateView(APIView):
    def get(self, request: Request) -> Response:
        return Response(
            {
                "language": translation.get_language(),
                "time": serializers.DateTimeField().to_representation(WHEN),
            }
        )

    def post(self, request: Request, slug: str = "") -> Response:
        return Response({"size": len(request.body)})


handler404 = "api.errors.not_found"
urlpatterns = [
    path("", include("config.urls_public")),
    path("api/test/state", StateView.as_view()),
    path("api/problems/<slug:slug>/submissions", StateView.as_view()),
]


@pytest.fixture(autouse=True)
def public_with_test_views(settings: SettingsWrapper) -> None:
    use_role(settings, "public", urls=__name__)


def state(query: str = "", client: EdgeClient | None = None) -> dict[str, str]:
    return (client or EdgeClient()).get(f"/api/test/state{query}").json()  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("query", "language"), [("", "th"), ("?lang=en", "en"), ("?lang=fr", "th")]
)
def test_language_from_query_only(query: str, language: str) -> None:
    assert state(query)["language"] == language


def test_old_language_cookie_ignored() -> None:
    client = EdgeClient()
    client.cookies["g_lang"] = "en"
    assert state(client=client)["language"] == "th"


def test_times_are_utc_with_z() -> None:
    assert state()["time"] == "2026-10-26T09:58:00Z"
    assert timezone.get_current_timezone_name() == "Asia/Bangkok"  # not left switched


def test_no_store_by_default_but_photos_keep_their_caching() -> None:
    assert EdgeClient().get("/api/site")["Cache-Control"] == "no-store"
    assert EdgeClient().get("/api/nope")["Cache-Control"] == "no-store"
    assert "no-store" not in EdgeClient().get("/").get("Cache-Control", "")


def post(url: str, size: int) -> int:
    client = EdgeClient()
    token = client.get("/api/auth/me").json()["csrf_token"]
    data = "x" * size
    return client.post(url, data, "text/plain", HTTP_X_CSRFTOKEN=token).status_code


def test_body_limit() -> None:
    assert post("/api/test/state", 16 * 1024) != 413
    assert post("/api/test/state", 16 * 1024 + 1) == 413
    assert post("/api/problems/aplusb/submissions", 100 * 1024) != 413
    assert post("/api/problems/aplusb/submissions", 256 * 1024 + 1) == 413


def test_too_big_is_json() -> None:
    client = EdgeClient()
    response = client.post("/api/test/state", "x" * 20000, "text/plain")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response["Cache-Control"] == "no-store"


def test_bad_content_length_is_too_big() -> None:
    response = EdgeClient().generic("POST", "/api/test/state", b"", CONTENT_LENGTH="abc")
    assert response.status_code == 413
