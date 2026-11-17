"""Submissions API (contract §9): per-state fields, privacy, rounding, lists, filters, source."""

from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client
from pytest_django import DjangoAssertNumQueries

from accounts.models import User
from judging.models import Submission
from problems.models import Problem
from tests.conftest import EdgeClient
from tests.test_submission_detail import SOURCE, add_test, make_submission, signed_in

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

DONE = Submission.Status.DONE
WHEN = datetime(2026, 10, 26, 10, 2, 14, tzinfo=UTC)


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice", display_name="Alice")


def detail(submission: Submission, client: Client | None = None) -> dict[str, Any]:
    response = (client or EdgeClient()).get(f"/api/submissions/{submission.pk}")
    assert response.status_code == 200, response.content
    return response.json()  # type: ignore[no-any-return]


def done(user: User, **fields: Any) -> Submission:
    values = {
        "status": DONE,
        "verdict": "PA",
        "score": 30.0,
        "judged_at": WHEN,
        "max_time_ms": 484,
        "max_memory_kb": 12697,
        "subtask_scores": [{"name": "1", "points": 30.0, "score": 30.0}],
        **fields,
    }
    return make_submission(user, **values)


def test_done_shape_and_rounding(alice: User) -> None:
    submission = done(alice)
    add_test(submission, "1-01", time_ms=14, memory_kb=3174)
    data = detail(submission)
    assert data["status"] == "done"
    assert (data["verdict"], data["score"]) == ("PA", 30)
    assert data["judged_at"] == "2026-10-26T10:02:14Z"
    assert data["created_at"].endswith("Z")
    assert (data["max_time_ms"], data["max_memory_mb"]) == (480, 12.4)
    assert data["subtasks"] == [{"name": "1", "points": 30, "score": 30}]
    assert data["tests"] == [
        {
            "name": "1-01",
            "verdict": "AC",
            "outcome": 1,
            "time_ms": 10,
            "memory_mb": 3.1,
            "message": "",
        }
    ]
    assert data["user"] == {"username": "alice", "display_name": "Alice"}
    assert (data["compile_log"], data["is_author"]) == (None, False)
    assert "source" not in data


@pytest.mark.parametrize("status", [Submission.Status.QUEUED, Submission.Status.JUDGING])
def test_waiting_hides_stale_values(alice: User, status: str) -> None:
    """Even if the row still holds an old result, nothing of it shows before `done`."""
    submission = done(alice, status=status, compile_log="old")
    add_test(submission, "1-01")
    data = detail(submission)
    assert data["verdict"] == ""
    assert data["score"] is None
    assert data["judged_at"] is None
    assert (data["max_time_ms"], data["max_memory_mb"]) == (None, None)
    assert data["subtasks"] == []
    assert data["compile_log"] is None
    assert len(data["tests"]) == (0 if status == Submission.Status.QUEUED else 1)
    assert "max_score" in data
    assert "is_author" in data


def test_compile_log_only_for_the_author_and_ce(alice: User) -> None:
    ce = done(
        alice, verdict="CE", score=0.0, max_time_ms=0, max_memory_kb=0, compile_log="a.cpp:1: error"
    )
    assert detail(ce)["compile_log"] is None
    mine = detail(ce, signed_in(alice))
    assert (mine["compile_log"], mine["is_author"]) == ("a.cpp:1: error", True)
    assert (mine["max_time_ms"], mine["max_memory_mb"]) == (None, None)  # no test ran
    wa = done(alice, slug="other", verdict="WA", compile_log="warning")
    assert detail(wa, signed_in(alice))["compile_log"] is None


def test_hidden_problem_is_404(alice: User) -> None:
    submission = done(alice)
    Problem.objects.filter(pk=submission.problem_id).update(is_visible=False)
    response = EdgeClient().get(f"/api/submissions/{submission.pk}")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")


def test_source_author_only(alice: User) -> None:
    submission = done(alice)
    url = f"/api/submissions/{submission.pk}/source"
    response = signed_in(alice).get(url)
    assert (response.status_code, response["Content-Type"]) == (200, "text/plain; charset=utf-8")
    assert response.content.decode() == SOURCE
    bob = User.objects.create_user(username="bob")
    assert signed_in(bob).get(url).status_code == 404
    assert EdgeClient().get(url).status_code == 404


def test_poll_query_count(
    alice: User, django_assert_max_num_queries: DjangoAssertNumQueries
) -> None:
    submission = done(alice, status=Submission.Status.JUDGING)
    add_test(submission, "1-01")
    client = signed_in(alice)
    with django_assert_max_num_queries(4):
        detail(submission, client)


def rows(query: str = "", client: Client | None = None, path: str = "/api/submissions") -> Any:
    response = (client or EdgeClient()).get(path + query)
    assert response.status_code == 200, response.content
    return response.json()


def test_list_newest_first_without_private_fields(alice: User) -> None:
    first, second = done(alice, slug="a"), done(alice, slug="b")
    data = rows()
    assert [r["id"] for r in data["results"]] == [second.pk, first.pk]
    assert data["results"][0].keys() == {
        "id", "problem", "user", "language", "status", "verdict", "score",
        "max_time_ms", "max_memory_mb", "created_at",
    }  # fmt: skip


def test_list_filters(alice: User) -> None:
    bob = User.objects.create_user(username="bob")
    done(alice, slug="a")
    done(bob, slug="b", verdict="AC")
    make_submission(bob, slug="c", verdict="AC")  # an old verdict on a queued rejudge

    def users(query: str) -> list[str]:
        return [r["user"]["username"] for r in rows(query)["results"]]

    assert users("?problem=a") == ["alice"]
    assert users("?user=bob") == ["bob", "bob"]
    assert users("?verdict=AC") == ["bob"]


@pytest.mark.parametrize("query", ["?verdict=XX", "?problem=no%20slug", "?user=" + "x" * 21])
def test_bad_filter_is_400(query: str) -> None:
    response = EdgeClient().get("/api/submissions" + query)
    assert (response.status_code, response.json()["error"]["code"]) == (400, "validation_error")


def test_hidden_problems_not_listed(alice: User) -> None:
    submission = done(alice)
    Problem.objects.filter(pk=submission.problem_id).update(is_visible=False)
    assert rows()["count"] == 0


def test_mine(alice: User) -> None:
    done(alice, slug="a")
    done(alice, slug="b")
    done(User.objects.create_user(username="bob"), slug="c")
    client = signed_in(alice)
    assert rows(client=client, path="/api/submissions/mine")["count"] == 2
    assert rows("?problem=a", client, "/api/submissions/mine")["count"] == 1
    response = EdgeClient().get("/api/submissions/mine")
    assert (response.status_code, response.json()["error"]["code"]) == (401, "not_authenticated")
