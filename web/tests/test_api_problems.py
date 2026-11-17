"""`GET /api/problems` (contract §7). Filtering and sorting live in problems/tasks.py and are
covered in depth by test_tasks_page.py; these tests check the API's shape and wiring."""

from typing import Any

import pytest
from pytest_django import DjangoAssertNumQueries

from accounts.models import User
from problems.models import Problem, ProblemGroup
from tests.conftest import EdgeClient
from tests.test_tasks_page import best, make_problem, signed_in

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def get(query: str = "", client: EdgeClient | None = None) -> dict[str, Any]:
    response = (client or EdgeClient()).get("/api/problems" + query)
    assert response.status_code == 200, response.content
    return response.json()  # type: ignore[no-any-return]


def test_signed_out_shape() -> None:
    make_problem("aplusb", title="A plus B")
    make_problem("hidden", visible=False)
    assert get("?lang=en") == {
        "count": 1,
        "page": 1,
        "page_size": 50,
        "counts": {"all": 1, "solved": None, "tried": None, "untried": None, "today": None},
        "results": [
            {
                "id": Problem.objects.get(slug="aplusb").pk,
                "slug": "aplusb",
                "title": "A plus B",
                "max_score": 100,
                "time_limit_ms": 1000,
                "memory_limit_mb": 256,
                "passes": 0,
            }
        ],
    }


def test_signed_in_counts_status_and_my_best() -> None:
    alice = User.objects.create_user(username="alice")
    solved, tried = make_problem("solved"), make_problem("tried")
    make_problem("untried")
    best(alice, solved, 100, days_ago=0)
    best(alice, tried, 40)
    body = get(client=signed_in(alice))
    assert body["counts"] == {"all": 3, "solved": 1, "tried": 1, "untried": 1, "today": 1}
    rows = {r["slug"]: (r["my_best"], r["status"], r["passes"]) for r in body["results"]}
    assert rows == {
        "solved": (100, "solved", 1),
        "tried": (40, "tried", 0),
        "untried": (None, "untried", 0),
    }


def test_filters_reach_the_query() -> None:
    alice = User.objects.create_user(username="alice")
    group = ProblemGroup.objects.create(slug="g", name_th="กลุ่ม")
    a = make_problem("alpha")
    make_problem("beta")
    a.group = group
    a.save(update_fields=["group"])
    best(alice, a, 100)
    client = signed_in(alice)

    def slugs(query: str) -> list[str]:
        return [r["slug"] for r in get(query, client)["results"]]

    assert slugs("?q=alp") == ["alpha"]
    assert slugs(f"?group={group.pk}") == ["alpha"]
    assert slugs("?status=untried") == ["beta"]
    assert slugs("?sort=-id") == ["beta", "alpha"]
    assert slugs("?sort=nonsense") == ["alpha", "beta"]
    body = get("?status=solved", client)
    assert body["count"] == 1
    assert body["counts"]["all"] == 2  # counts ignore the filters


def test_status_ignored_signed_out() -> None:
    make_problem("a")
    assert get("?status=solved")["count"] == 1


def test_bad_page_and_past_the_end() -> None:
    make_problem("a")
    assert EdgeClient().get("/api/problems?page=0").status_code == 400
    body = get("?page=2")
    assert (body["count"], body["results"]) == (1, [])


def test_fixed_query_count(django_assert_max_num_queries: DjangoAssertNumQueries) -> None:
    alice = User.objects.create_user(username="alice")
    for i in range(20):
        best(alice, make_problem(f"p{i}"), 50)
    client = signed_in(alice)
    with django_assert_max_num_queries(8):
        assert len(get(client=client)["results"]) == 20
