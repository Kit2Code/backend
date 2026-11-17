"""`GET /api/ranking` and `GET /api/users/{username}` (contract §10). Ranking and title rules are
covered by test_ranking.py and test_profile.py; these check the API's shape and wiring."""

from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import User
from judging.models import UserProblemBest
from tests.conftest import EdgeClient
from tests.test_archive import make_group, make_problem, solve

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def get(path: str, client: Client | None = None, status: int = 200) -> Any:
    response = (client or EdgeClient()).get(path)
    assert response.status_code == status, (path, response.content)
    return response.json()


def test_ranking_shape_and_rank_across_pages() -> None:
    problem = make_problem("p", None)
    now = timezone.now()
    UserProblemBest.objects.bulk_create(
        UserProblemBest(
            user=User.objects.create_user(username=f"u{i:03}", display_name=f"U{i}"),
            problem=problem,
            best_score=100 - i * 0.5,
            reached_at=now,
        )
        for i in range(101)
    )
    first = get("/api/ranking")
    assert (first["count"], first["page_size"], len(first["results"])) == (101, 100, 100)
    assert first["results"][0] == {
        "rank": 1, "username": "u000", "display_name": "U0", "photo_url": None, "total": 100,
    }  # fmt: skip
    second = get("/api/ranking?page=2")
    assert [(r["rank"], r["username"]) for r in second["results"]] == [(101, "u100")]
    assert get("/api/ranking?page=0", status=400)["error"]["code"] == "validation_error"


def test_profile() -> None:
    alice = User.objects.create_user(username="alice", display_name="Alice")
    toi = make_group("toi")
    solve(alice, make_problem("b", toi))
    solve(alice, make_problem("a", toi))
    solve(alice, make_problem("c", None), score=30)
    make_problem("hidden", None, visible=False)
    data = get("/api/users/alice?lang=en")
    assert data["title"] == {"key": "newbie", "name": "Newbie"}
    assert data["standing"] == {"total": 230, "rank": 1}
    assert [r["slug"] for r in data["solved"]] == ["a", "b"]
    assert data["solved"][0]["reached_at"].endswith("Z")
    assert [(r["slug"], r["best_score"]) for r in data["attempted"]] == [("c", 30)]
    assert [(f["slug"], f["total"], f["solved"]) for f in data["folders"]] == [("toi", 2, 2)]
    assert data["is_self"] is False
    me = EdgeClient()
    me.force_login(alice)
    assert get("/api/users/alice", me)["is_self"] is True
    assert get("/api/users/alice")["title"]["name"] != "Newbie"  # Thai by default


def test_profile_without_score_has_no_rank() -> None:
    User.objects.create_user(username="new")
    assert get("/api/users/new")["standing"] == {"total": 0, "rank": None}


def test_unknown_or_disabled_user_is_404() -> None:
    User.objects.create_user(username="gone", is_active=False)
    for name in ["gone", "nobody"]:
        assert get(f"/api/users/{name}", status=404)["error"]["code"] == "not_found"
