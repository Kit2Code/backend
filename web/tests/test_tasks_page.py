"""The Tasks page (/problems): stat cards, search, filters, sorting, rows, pages, query count."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone
from pytest_django import DjangoAssertNumQueries

from accounts.models import User
from judging.models import UserProblemBest
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def make_problem(
    slug: str, *, visible: bool = True, title: str = "", max_score: float = 100
) -> Problem:
    problem = Problem.objects.create(
        slug=slug,
        is_visible=visible,
        title_th=title or f"โจทย์ {slug}",
        title_en=title or f"Problem {slug}",
        max_score=max_score,
        time_limit_ms=1000,
        memory_limit_mb=256,
    )
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    problem.refresh_from_db()
    return problem


def best(user: User, problem: Problem, score: float, days_ago: int = 3) -> None:
    UserProblemBest.objects.create(
        user=user,
        problem=problem,
        best_score=score,
        reached_at=timezone.now() - timedelta(days=days_ago),
    )


def signed_in(user: User) -> EdgeClient:
    client = EdgeClient()
    client.force_login(user)
    return client


def page(client: EdgeClient, query: str = "") -> str:
    response = client.get("/problems" + query)
    assert response.status_code == 200
    return response.content.decode()


def slugs_in_order(html: str) -> list[str]:
    return re.findall(r'<tr data-slug="([^"]+)"', html)


def stat(html: str, name: str) -> int:
    match = re.search(rf'<div class="stat stat-{name}">.*?<strong>(\d+)</strong>', html, re.S)
    assert match, f"no {name} card"
    return int(match.group(1))


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice")


@pytest.fixture
def five(alice: User) -> dict[str, Problem]:
    """a: solved today, b: solved long ago, c: tried, d and e: not submitted; plus a hidden one."""
    problems = {slug: make_problem(slug) for slug in "abcde"}
    make_problem("hidden", visible=False)
    best(alice, problems["a"], 100, days_ago=0)
    best(alice, problems["b"], 100)
    best(alice, problems["c"], 40)
    return problems


# ---- stat cards ----


def test_signed_out_sees_only_the_total(five: dict[str, Problem]) -> None:
    html = page(EdgeClient())
    assert stat(html, "all") == 5
    for name in ["solved", "tried", "untried", "today"]:
        assert f"stat-{name}" not in html


def test_signed_in_sees_their_own_counts(alice: User, five: dict[str, Problem]) -> None:
    html = page(signed_in(alice))
    assert stat(html, "all") == 5
    assert stat(html, "solved") == 2
    assert stat(html, "tried") == 1
    assert stat(html, "untried") == 2
    assert stat(html, "today") == 1


def test_counts_ignore_hidden_problems(alice: User) -> None:
    hidden = make_problem("hidden", visible=False)
    best(alice, hidden, 100)
    html = page(signed_in(alice))
    assert stat(html, "all") == 0
    assert stat(html, "solved") == 0


def test_full_marks_follow_each_problems_maximum(alice: User) -> None:
    small = make_problem("small", max_score=30)
    best(alice, small, 30)
    assert stat(page(signed_in(alice)), "solved") == 1


# ---- rows ----


def test_rows_show_id_title_limits_passes_and_submit(alice: User) -> None:
    problem = make_problem("aplusb", title="เอบวกบี")
    Problem.objects.filter(pk=problem.pk).update(time_limit_ms=1500, memory_limit_mb=64)
    bob = User.objects.create_user(username="bob")
    carol = User.objects.create_user(username="carol", is_active=False)
    best(alice, problem, 100)
    best(bob, problem, 100)
    best(carol, problem, 100)  # disabled accounts don't count
    best(User.objects.create_user(username="dave"), problem, 50)
    html = page(EdgeClient())
    row = re.search(r'<tr data-slug="aplusb">.*?</tr>', html, re.S)
    assert row, html
    text = row[0]
    assert f">{problem.pk}<" in text
    assert re.search(r'<a href="/problems/aplusb" class="task-title">เอบวกบี</a>', text)
    assert "1.5 วินาที" in text and "64 MB" in text
    assert re.search(r'<td class="passes">\s*2\s*</td>', text)
    assert 'href="/problems/aplusb/submit"' in text


def test_status_icons_for_the_signed_in_user(alice: User, five: dict[str, Problem]) -> None:
    html = page(signed_in(alice))
    statuses = dict(re.findall(r'<tr data-slug="([^"]+)".*?data-status="(\w+)"', html, re.S))
    assert statuses == {"a": "solved", "b": "solved", "c": "tried", "d": "untried", "e": "untried"}


def test_no_status_column_when_signed_out(five: dict[str, Problem]) -> None:
    assert "data-status" not in page(EdgeClient())


def test_hidden_problems_are_not_listed(five: dict[str, Problem]) -> None:
    assert "hidden" not in slugs_in_order(page(EdgeClient()))


# ---- search, filters, sorting ----


def test_search_matches_slug_and_both_titles() -> None:
    make_problem("aplusb", title="เอบวกบี")
    Problem.objects.filter(slug="aplusb").update(title_en="A plus B")
    make_problem("graph")
    assert slugs_in_order(page(EdgeClient(), "?q=plus")) == ["aplusb"]
    assert slugs_in_order(page(EdgeClient(), "?q=เอบวก")) == ["aplusb"]
    assert slugs_in_order(page(EdgeClient(), "?q=GRA")) == ["graph"]
    assert "ไม่พบโจทย์" in page(EdgeClient(), "?q=nothing-like-this")


def test_search_by_the_number_in_the_id_column() -> None:
    wanted = make_problem("graph")
    make_problem("other")
    assert slugs_in_order(page(EdgeClient(), f"?q={wanted.pk}")) == ["graph"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [("solved", ["a", "b"]), ("tried", ["c"]), ("untried", ["d", "e"]), ("", list("abcde"))],
)
def test_status_filter(
    alice: User, five: dict[str, Problem], status: str, expected: list[str]
) -> None:
    assert slugs_in_order(page(signed_in(alice), f"?status={status}")) == expected


def test_status_filter_is_ignored_when_signed_out(five: dict[str, Problem]) -> None:
    assert slugs_in_order(page(EdgeClient(), "?status=solved")) == list("abcde")


def test_sort_by_id_title_and_passes(alice: User) -> None:
    first = make_problem("zeta", title="Alpha")
    second = make_problem("alpha", title="Zulu")
    best(alice, second, 100)
    assert slugs_in_order(page(EdgeClient())) == ["zeta", "alpha"]  # by id
    assert slugs_in_order(page(EdgeClient(), "?sort=-id")) == ["alpha", "zeta"]
    assert slugs_in_order(page(EdgeClient(), "?sort=title")) == ["zeta", "alpha"]
    assert slugs_in_order(page(EdgeClient(), "?sort=-passes")) == ["alpha", "zeta"]
    assert slugs_in_order(page(EdgeClient(), "?sort=nonsense")) == ["zeta", "alpha"]
    assert first.pk < second.pk


def test_sort_links_keep_the_search() -> None:
    make_problem("graph")
    html = page(EdgeClient(), "?q=gra&sort=id")
    assert re.search(r'href="\?q=gra&amp;sort=-id"', html)


# ---- pages ----


def test_fifty_per_page() -> None:
    for number in range(55):
        make_problem(f"p{number:02d}")
    first = slugs_in_order(page(EdgeClient()))
    second = slugs_in_order(page(EdgeClient(), "?page=2"))
    assert len(first) == 50 and len(second) == 5
    assert not set(first) & set(second)
    assert 'href="?page=2"' in page(EdgeClient())


# ---- htmx ----


def test_htmx_search_gets_only_the_rows() -> None:
    make_problem("graph")
    response = EdgeClient().get("/problems?q=gra", headers={"HX-Request": "true"})
    html = response.content.decode()
    assert "<html" not in html
    assert slugs_in_order(html) == ["graph"]
    assert "HX-Request" in response["Vary"]


def test_search_box_works_with_and_without_javascript() -> None:
    html = page(EdgeClient())
    form = re.search(r'<form[^>]*class="task-search"[^>]*>', html)
    assert form, html
    assert 'method="get"' in form[0]
    assert 'hx-get="/problems"' in form[0]
    assert 'hx-target="#task-results"' in form[0]
    assert "/static/vendor/htmx/htmx.min.js" in html


# ---- speed ----


@pytest.mark.parametrize("count", [3, 40])
def test_query_count_does_not_grow_with_problems(
    alice: User, count: int, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    for number in range(count):
        problem = make_problem(f"p{number:02d}")
        best(alice, problem, 100 if number % 2 else 10)
    client = signed_in(alice)
    client.get("/problems")  # warm the session and site settings
    with django_assert_num_queries(TASKS_PAGE_QUERIES):
        client.get("/problems")


# session, user, site settings, the stat cards (one query), the groups for the filter, the page
# count, the page's rows (with the user's own status on each)
TASKS_PAGE_QUERIES = 7
