"""The ranking (sum of best scores, earlier finish first) and the profile page (solved list)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from judging.models import UserProblemBest
from problems.models import Problem
from tests.conftest import EdgeClient
from tests.test_problem_pages import FIXTURES, import_fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


@pytest.fixture
def problems(tmp_path: Path, settings: SettingsWrapper) -> dict[str, Problem]:
    """aplusb (100 points) and groupmin (100 points), visible, plus python-only kept hidden."""
    folder = tmp_path / "problems"
    folder.mkdir()
    settings.GRADER_PROBLEMS_DIR = folder
    return {
        "aplusb": import_fixture(FIXTURES / "problems" / "aplusb", "--visible"),
        "groupmin": import_fixture(FIXTURES / "problems" / "groupmin", "--visible"),
        "hidden": import_fixture(FIXTURES / "problems" / "python-only"),
    }


def at(hour: int) -> datetime:
    return datetime(2026, 10, 1, hour, tzinfo=UTC)


def best(user: User, problem: Problem, score: float, hour: int) -> None:
    UserProblemBest.objects.create(
        user=user, problem=problem, best_score=score, reached_at=at(hour)
    )


def user(name: str, **fields: object) -> User:
    return User.objects.create_user(username=name, **fields)  # type: ignore[arg-type]


def ranked(html: str) -> list[str]:
    """The usernames in ranking order (each links to its profile)."""
    return re.findall(r'href="/users/([a-z0-9_]+)"', html)


def get(url: str) -> str:
    response = EdgeClient().get(url)
    assert response.status_code == 200
    return response.content.decode()


# ---- ranking ----


def test_sum_of_best_scores_highest_first(problems: dict[str, Problem]) -> None:
    alice, bob, carol = user("alice"), user("bob"), user("carol")
    best(alice, problems["aplusb"], 100, 1)
    best(bob, problems["aplusb"], 100, 2)
    best(bob, problems["groupmin"], 40, 3)
    best(carol, problems["groupmin"], 60, 4)
    html = get("/ranking")
    assert ranked(html) == ["bob", "alice", "carol"]
    assert "140" in html


def test_ties_go_to_whoever_got_there_first(problems: dict[str, Problem]) -> None:
    early, late = user("early"), user("late")
    best(late, problems["aplusb"], 100, 1)  # reached its first 100 earlier...
    best(late, problems["groupmin"], 50, 9)  # ...but its total only at 09:00
    best(early, problems["groupmin"], 100, 2)
    best(early, problems["aplusb"], 50, 5)
    assert ranked(get("/ranking")) == ["early", "late"]


def test_equal_score_and_time_falls_back_to_the_username(problems: dict[str, Problem]) -> None:
    for name in ("zed", "amy"):
        best(user(name), problems["aplusb"], 100, 1)
    assert ranked(get("/ranking")) == ["amy", "zed"]


def test_hidden_problems_disabled_users_and_zero_scores_do_not_count(
    problems: dict[str, Problem],
) -> None:
    alice, gone, zero = user("alice"), user("gone", is_active=False), user("zero")
    best(alice, problems["aplusb"], 30, 1)
    best(alice, problems["hidden"], 100, 1)
    best(gone, problems["aplusb"], 100, 1)
    best(zero, problems["aplusb"], 0, 1)
    html = get("/ranking")
    assert ranked(html) == ["alice"]
    assert ">30<" in html


def test_ranking_is_one_query_for_the_rows(
    problems: dict[str, Problem], django_assert_max_num_queries: Callable[..., object]
) -> None:
    for n in range(30):
        best(user(f"user{n:02d}"), problems["aplusb"], n + 1, 1)
    # The rows, the paginator's count, and the site settings every page reads (with a savepoint).
    with django_assert_max_num_queries(6):  # type: ignore[attr-defined]
        assert len(ranked(get("/ranking"))) == 30


def test_ranking_shows_display_name_and_school(problems: dict[str, Problem]) -> None:
    best(user("alice", display_name="Alice A.", school="Some School"), problems["aplusb"], 1, 1)
    html = get("/ranking")
    assert "Alice A." in html and "Some School" in html


def test_navigation_links_to_the_ranking() -> None:
    assert 'href="/ranking"' in get("/")


# ---- profile ----


def test_profile_lists_solved_and_attempted(problems: dict[str, Problem]) -> None:
    alice = user("alice", display_name="Alice A.")
    best(alice, problems["aplusb"], 100, 1)
    best(alice, problems["groupmin"], 40, 2)
    best(alice, problems["hidden"], 100, 3)
    html = get("/users/alice")
    assert "Alice A." in html
    solved, attempted = html.split('id="attempted"')
    assert 'href="/problems/aplusb"' in solved
    assert 'href="/problems/groupmin"' in attempted
    assert "40" in attempted
    assert "python-only" not in html  # hidden problems stay hidden
    assert 'href="/submissions?user=alice"' in html


def test_profile_of_unknown_or_disabled_user_is_404() -> None:
    user("gone", is_active=False)
    assert EdgeClient().get("/users/nobody").status_code == 404
    assert EdgeClient().get("/users/gone").status_code == 404
