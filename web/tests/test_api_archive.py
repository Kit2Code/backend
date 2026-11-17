"""`GET /api/groups…` (contract §8). Counting rules are covered by test_archive.py."""

from typing import Any

import pytest
from pytest_django import DjangoAssertNumQueries

from accounts.models import User
from tests.conftest import EdgeClient
from tests.test_archive import make_group, make_problem, solve

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def get(path: str, client: EdgeClient | None = None, status: int = 200) -> Any:
    response = (client or EdgeClient()).get(path)
    assert response.status_code == status, (path, response.content)
    return response.json()


@pytest.fixture
def tree() -> User:
    """toi (1 own problem) > toi20 (2 problems), empty group, hidden-only group, 1 ungrouped."""
    toi = make_group("toi")
    toi20 = make_group("toi20", parent=toi)
    make_group("empty")
    hidden = make_group("hidden")
    alice = User.objects.create_user(username="alice")
    solve(alice, make_problem("own", toi))
    make_problem("a", toi20)
    make_problem("b", toi20)
    make_problem("h", hidden, visible=False)
    make_problem("loose", None)
    return alice


def test_top_level(tree: User) -> None:
    folders = get("/api/groups?lang=en")["folders"]
    assert [(f["slug"], f["name"], f["total"], f["solved"]) for f in folders] == [
        ("toi", "Group toi", 3, None),
        ("other", "Other", 1, None),
    ]
    assert folders[1]["id"] is None
    client = EdgeClient()
    client.force_login(tree)
    assert get("/api/groups", client)["folders"][0]["solved"] == 1


def test_group_with_subgroups_and_own_problems(tree: User) -> None:
    data = get("/api/groups/toi?lang=en")
    assert data["group"]["slug"] == "toi"
    assert data["crumbs"] == []
    assert [(f["slug"], f["total"]) for f in data["folders"]] == [("toi20", 2)]
    assert [r["slug"] for r in data["problems"]["results"]] == ["own"]
    assert data["problems"]["page_size"] == 50


def test_subgroup(tree: User) -> None:
    data = get("/api/groups/toi/toi20?lang=en")
    assert data["crumbs"] == [{"slug": "toi", "name": "Group toi"}]
    assert data["folders"] == []
    assert data["problems"]["count"] == 2
    assert [r["slug"] for r in get("/api/groups/toi/toi20?sort=-id")["problems"]["results"]] == [
        "b",
        "a",
    ]


def test_top_group_with_only_subgroup_problems_has_null_problems() -> None:
    top = make_group("top")
    make_problem("x", make_group("sub", parent=top))
    data = get("/api/groups/top")
    assert data["problems"] is None
    assert len(data["folders"]) == 1


def test_other(tree: User) -> None:
    data = get("/api/groups/other")
    assert data["group"]["id"] is None
    assert [r["slug"] for r in data["problems"]["results"]] == ["loose"]


@pytest.mark.parametrize(
    "path",
    ["/api/groups/empty", "/api/groups/hidden", "/api/groups/nope", "/api/groups/toi20",
     "/api/groups/toi/nope", "/api/groups/other/x"],
)  # fmt: skip
def test_404s(tree: User, path: str) -> None:
    assert get(path, status=404)["error"]["code"] == "not_found"


def test_query_count(tree: User, django_assert_max_num_queries: DjangoAssertNumQueries) -> None:
    client = EdgeClient()
    client.force_login(tree)
    with django_assert_max_num_queries(8):
        get("/api/groups/toi", client)
