"""The API's error shape (contract §12) and pages (contract §11)."""

from typing import Any

import pytest
from django.http import Http404
from django.test import Client, RequestFactory
from rest_framework import exceptions
from rest_framework.request import Request

from api.errors import handler, server_error
from api.pagination import Page

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def error_of(exc: Exception) -> tuple[int, dict[str, Any], Any]:
    response = handler(exc, {})
    assert response is not None
    return response.status_code, response.data["error"], response


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (exceptions.ParseError(), 400, "validation_error"),
        (exceptions.NotAuthenticated(), 401, "not_authenticated"),
        (exceptions.AuthenticationFailed(), 401, "not_authenticated"),
        (exceptions.PermissionDenied("bad token", code="csrf_failed"), 403, "csrf_failed"),
        (exceptions.NotFound(), 404, "not_found"),
        (Http404(), 404, "not_found"),
        (exceptions.MethodNotAllowed("PUT"), 405, "method_not_allowed"),
        (exceptions.UnsupportedMediaType("text/plain"), 415, "unsupported_media_type"),
        (exceptions.Throttled(), 429, "rate_limited"),
        (exceptions.Throttled(code="cooldown"), 429, "cooldown"),
    ],
)
def test_codes(exc: Exception, status: int, code: str) -> None:
    got_status, error, _ = error_of(exc)
    assert (got_status, error["code"]) == (status, code)
    assert error["message"]
    assert "fields" not in error


def test_validation_error_has_fields_and_first_message() -> None:
    exc = exceptions.ValidationError({"source": ["Please type or upload your code."]})
    status, error, _ = error_of(exc)
    assert status == 400
    assert error == {
        "code": "validation_error",
        "message": "Please type or upload your code.",
        "fields": {"source": ["Please type or upload your code."]},
    }


def test_retry_after_kept() -> None:
    _, _, response = error_of(exceptions.Throttled(wait=7))
    assert response["Retry-After"] == "7"


def test_unknown_exception_left_to_500() -> None:
    assert handler(ValueError("boom"), {}) is None


def test_wrong_method_on_a_real_endpoint(client: Client) -> None:
    response = client.post("/api/site", {}, content_type="application/json")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_unknown_api_url_is_json_404(client: Client) -> None:
    response = client.get("/api/no-such-thing")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


def test_old_pages_keep_html_404(client: Client) -> None:
    response = client.get("/problems/no-such-problem")
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")


def test_api_500_is_json() -> None:
    response = server_error(RequestFactory().get("/api/site"))
    assert response.status_code == 500
    assert b'"server_error"' in response.content


def page_of(query: str, items: list[int]) -> Any:
    paginator = Page()
    request = Request(RequestFactory().get(f"/api/x{query}"))
    rows = paginator.paginate_queryset(items, request)
    return paginator.get_paginated_response(rows).data


# Not named `page`: that is pytest-playwright's browser fixture.
@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "last", "", " 1", "01"])
def test_bad_page_is_400(raw: str) -> None:
    with pytest.raises(exceptions.ValidationError) as caught:
        page_of(f"?page={raw}", list(range(10)))
    _, error, _ = error_of(caught.value)
    assert error["code"] == "validation_error"
    assert "page" in error["fields"]


def test_pages() -> None:
    items = list(range(120))
    assert page_of("", items) == {
        "count": 120,
        "page": 1,
        "page_size": 50,
        "results": list(range(50)),
    }
    assert page_of("?page=3", items)["results"] == list(range(100, 120))


@pytest.mark.parametrize("raw", ["4", "999", "9" * 30])
def test_past_the_end_is_empty_with_real_count(raw: str) -> None:
    data = page_of(f"?page={raw}", list(range(120)))
    assert (data["count"], data["page"], data["results"]) == (120, int(raw), [])


def test_queryset_past_the_end_does_not_hit_huge_offset() -> None:
    from problems.models import Problem

    paginator = Page()
    request = Request(RequestFactory().get("/api/x?page=" + "9" * 30))
    assert paginator.paginate_queryset(Problem.objects.all(), request) == []
