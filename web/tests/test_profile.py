"""The Codeforces-style profile page: title by solved count, rank and score, solved-over-time chart,
group progress, solved and tried lists, and a link to the settings on your own profile."""

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from pytest_django import DjangoAssertNumQueries

from accounts.models import User
from judging import charts
from judging.models import UserProblemBest
from judging.profile import TITLES, title_for
from problems.models import Problem, ProblemGroup, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def make_problem(slug: str, group: ProblemGroup | None = None, visible: bool = True) -> Problem:
    problem = Problem.objects.create(
        slug=slug, is_visible=visible, group=group, max_score=100, title_th=f"โจทย์ {slug}"
    )
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    return problem


def best(user: User, problem: Problem, score: float, day: int = 1) -> None:
    UserProblemBest.objects.create(
        user=user,
        problem=problem,
        best_score=score,
        reached_at=datetime(2026, 9, day, 12, tzinfo=UTC),
    )


def page(path: str, client: EdgeClient | None = None) -> str:
    response = (client or EdgeClient()).get(path)
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice", display_name="Alice A.", school="Test School")


# ---- titles ----


@pytest.mark.parametrize(
    ("solved", "key"),
    [(0, "newbie"), (9, "newbie"), (10, "pupil"), (29, "pupil"), (30, "specialist"),
     (74, "specialist"), (75, "expert"), (149, "expert"), (150, "master"), (900, "master")],
)  # fmt: skip
def test_title_by_solved_count(solved: int, key: str) -> None:
    assert title_for(solved).key == key


def test_titles_are_in_order() -> None:
    limits = [title.min_solved for title in TITLES]
    assert limits == sorted(limits) and limits[0] == 0


# ---- the page ----


def test_profile_shows_title_name_rank_score_and_counts(alice: User) -> None:
    bob = User.objects.create_user(username="bob")
    problems = [make_problem(f"p{n}") for n in range(4)]
    best(bob, problems[0], 100)
    best(bob, problems[1], 100)
    best(bob, problems[2], 100)  # bob: 300, first
    best(alice, problems[0], 100)
    best(alice, problems[1], 40)  # alice: 140, solved 1, tried 1
    html = page("/users/alice")
    assert re.search(r'<p class="rank-title title-newbie">', html)
    assert re.search(r'<h1 class="title-newbie">Alice A\.</h1>', html)
    assert "@alice" in html and "Test School" in html
    assert re.search(r'data-rank="2"', html)
    assert re.search(r'data-score="140"', html)
    assert re.search(r'data-solved="1"', html)
    assert re.search(r'data-tried="1"', html)


def test_a_user_with_no_score_has_no_rank(alice: User) -> None:
    assert 'data-rank="' not in page("/users/alice")


def test_member_since_and_last_active(alice: User) -> None:
    User.objects.filter(pk=alice.pk).update(
        date_joined=datetime(2026, 1, 2, tzinfo=UTC), last_login=datetime(2026, 10, 3, tzinfo=UTC)
    )
    html = page("/users/alice")
    assert "2026-01-02" in html and "2026-10-03" in html


def test_own_profile_links_the_settings(alice: User) -> None:
    assert 'href="/settings"' not in page("/users/alice")
    client = EdgeClient()
    client.force_login(alice)
    html = page("/users/alice", client)
    assert re.search(r'<main>.*href="/settings"', html, re.S)


def test_profile_shows_the_solved_over_time_chart(alice: User) -> None:
    for day, slug in enumerate(["a", "b", "c"], start=1):
        best(alice, make_problem(slug), 100, day=day)
    html = page("/users/alice")
    chart = re.search(r'<svg class="chart"[^>]*role="img"[^>]*>.*?</svg>', html, re.S)
    assert chart
    assert "<polyline" in chart[0]


def test_no_chart_before_the_first_solve(alice: User) -> None:
    assert '<svg class="chart"' not in page("/users/alice")


def test_profile_shows_progress_per_group(alice: User) -> None:
    toi = ProblemGroup.objects.create(slug="toi", name_th="TOI")
    sub = ProblemGroup.objects.create(slug="toi20", name_th="TOI20", parent=toi)
    best(alice, make_problem("t1", sub), 100)
    make_problem("t2", sub)
    html = page("/users/alice")
    assert re.search(
        r'<a class="folder-card" href="/archive/toi" data-total="2" data-solved="1"', html
    )


def test_hidden_problems_do_not_count(alice: User) -> None:
    best(alice, make_problem("secret", visible=False), 100)
    html = page("/users/alice")
    assert "secret" not in html
    assert re.search(r'data-solved="0"', html)


def test_profile_query_count_does_not_grow(
    alice: User, django_assert_max_num_queries: DjangoAssertNumQueries
) -> None:
    for number in range(30):
        best(alice, make_problem(f"p{number}"), 100 if number % 2 else 50, day=1 + number % 28)
    EdgeClient().get("/users/alice")
    with django_assert_max_num_queries(10):
        EdgeClient().get("/users/alice")


# ---- the chart itself ----


def test_chart_of_nothing_is_none() -> None:
    assert charts.solved_over_time([]) is None


def test_chart_climbs_one_step_per_solve_from_left_to_right() -> None:
    days = [date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 5), date(2026, 9, 30)]
    chart = charts.solved_over_time(days)
    assert chart is not None
    xs = [x for x, _y in chart.points]
    ys = [y for _x, y in chart.points]
    assert xs == sorted(xs)
    assert ys == sorted(ys, reverse=True)  # SVG y grows downwards: more solved is higher up
    assert xs[0] == chart.left and xs[-1] == chart.right
    assert chart.total == 4


def test_chart_of_one_day_still_draws_a_line() -> None:
    chart = charts.solved_over_time([date(2026, 9, 1)])
    assert chart is not None and len(chart.points) >= 2


def test_chart_points_attribute_is_plain_numbers() -> None:
    chart = charts.solved_over_time([date(2026, 9, 1) + timedelta(days=n) for n in range(50)])
    assert chart is not None
    assert re.fullmatch(r"[\d. ,]+", chart.points_attribute)
