"""The Archive: folder cards for two-level problem groups, with per-user progress."""

import re

import pytest
from django.utils import timezone
from pytest_django import DjangoAssertNumQueries

from accounts.models import User
from judging.models import UserProblemBest
from problems.models import Problem, ProblemGroup, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

Groups = dict[str, ProblemGroup]


def make_group(slug: str, parent: ProblemGroup | None = None, order: int = 0) -> ProblemGroup:
    return ProblemGroup.objects.create(
        slug=slug, name_th=f"กลุ่ม {slug}", name_en=f"Group {slug}", parent=parent, order=order
    )


def make_problem(slug: str, group: ProblemGroup | None, *, visible: bool = True) -> Problem:
    problem = Problem.objects.create(
        slug=slug, is_visible=visible, group=group, max_score=100, time_limit_ms=1000,
        memory_limit_mb=256,
    )  # fmt: skip
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    return problem


def solve(user: User, problem: Problem, score: float = 100) -> None:
    UserProblemBest.objects.create(
        user=user, problem=problem, best_score=score, reached_at=timezone.now()
    )


def get(client: EdgeClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.content.decode()


def cards(html: str) -> dict[str, dict[str, str]]:
    """Each folder card's link and its data-* attributes."""
    found = {}
    for tag in re.findall(r'<a class="folder-card"[^>]*>', html):
        href = re.search(r'href="([^"]+)"', tag)
        assert href
        found[href.group(1)] = dict(re.findall(r'data-(\w+)="([^"]*)"', tag))
    return found


def rows(html: str) -> list[str]:
    return re.findall(r'<tr data-slug="([^"]+)"', html)


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice")


@pytest.fixture
def toi() -> Groups:
    """TOI (TOI19: 2 problems, TOI20: 3, one hidden), IOI (1 problem), an empty group,
    and one problem with no group."""
    toi = make_group("toi", order=1)
    toi19 = make_group("toi19", parent=toi)
    toi20 = make_group("toi20", parent=toi)
    ioi = make_group("ioi", order=2)
    make_group("empty", order=3)
    for slug in ["t19a", "t19b"]:
        make_problem(slug, toi19)
    for slug in ["t20a", "t20b", "t20c"]:
        make_problem(slug, toi20)
    make_problem("t20secret", toi20, visible=False)
    make_problem("ioi1", ioi)
    make_problem("loose", None)
    return {"toi": toi, "toi19": toi19, "toi20": toi20, "ioi": ioi}


def signed_in(user: User) -> EdgeClient:
    client = EdgeClient()
    client.force_login(user)
    return client


# ---- the top level ----


def test_top_level_cards_count_subgroups_and_skip_empty_groups(toi: Groups) -> None:
    found = cards(get(EdgeClient(), "/archive"))
    assert list(found) == ["/archive/toi", "/archive/ioi", "/archive/other"]
    assert found["/archive/toi"]["total"] == "5"  # hidden problems don't count
    assert found["/archive/ioi"]["total"] == "1"
    assert found["/archive/other"]["total"] == "1"
    assert "solved" not in found["/archive/toi"]  # signed out: counts only


def test_signed_in_cards_show_progress(alice: User, toi: Groups) -> None:
    solve(alice, Problem.objects.get(slug="t19a"))
    solve(alice, Problem.objects.get(slug="t20a"))
    solve(alice, Problem.objects.get(slug="t20b"), score=50)  # tried, not solved
    html = get(signed_in(alice), "/archive")
    found = cards(html)
    assert found["/archive/toi"]["solved"] == "2"
    assert found["/archive/ioi"]["solved"] == "0"
    assert re.search(r'<progress value="2" max="5"', html)
    assert "40%" in html


def test_no_other_card_when_every_problem_has_a_group() -> None:
    make_problem("p", make_group("toi"))
    assert "/archive/other" not in get(EdgeClient(), "/archive")


def test_names_follow_the_language(toi: Groups) -> None:
    client = EdgeClient()
    assert "กลุ่ม toi" in get(client, "/archive")
    client.post("/i18n/setlang/", {"language": "en", "next": "/"})
    assert "Group toi" in get(client, "/archive")


# ---- inside a group ----


def test_a_group_with_subgroups_shows_their_cards(toi: Groups) -> None:
    html = get(EdgeClient(), "/archive/toi")
    found = cards(html)
    assert list(found) == ["/archive/toi/toi19", "/archive/toi/toi20"]
    assert found["/archive/toi/toi20"]["total"] == "3"
    assert 'href="/archive"' in html  # the way back


def test_a_leaf_group_lists_its_problems(toi: Groups) -> None:
    html = get(EdgeClient(), "/archive/toi/toi20")
    assert rows(html) == ["t20a", "t20b", "t20c"]
    assert 'href="/archive/toi"' in html


def test_a_top_level_group_without_subgroups_lists_its_problems(toi: Groups) -> None:
    assert rows(get(EdgeClient(), "/archive/ioi")) == ["ioi1"]


def test_other_lists_problems_without_a_group(toi: Groups) -> None:
    assert rows(get(EdgeClient(), "/archive/other")) == ["loose"]


def test_leaf_rows_show_the_users_status(alice: User, toi: Groups) -> None:
    solve(alice, Problem.objects.get(slug="t20a"))
    html = get(signed_in(alice), "/archive/toi/toi20")
    assert re.search(r'<tr data-slug="t20a">.*?data-status="solved"', html, re.S)


@pytest.mark.parametrize(
    "path",
    [
        "/archive/nope",
        "/archive/toi20",
        "/archive/ioi/toi20",
        "/archive/toi/nope",
        "/archive/empty",
    ],
)
def test_unknown_or_misplaced_groups_are_404(toi: Groups, path: str) -> None:
    assert EdgeClient().get(path).status_code == 404


# ---- the Tasks page's group filter ----


def test_tasks_group_filter_includes_subgroups(toi: Groups) -> None:
    group = toi["toi"].pk
    assert rows(get(EdgeClient(), f"/problems?group={group}")) == [
        "t19a", "t19b", "t20a", "t20b", "t20c",
    ]  # fmt: skip
    assert rows(get(EdgeClient(), f"/problems?group={toi['toi20'].pk}")) == ["t20a", "t20b", "t20c"]
    assert rows(get(EdgeClient(), "/problems?group=nonsense")) == rows(
        get(EdgeClient(), "/problems")
    )


def test_tasks_page_offers_the_groups(toi: Groups) -> None:
    html = get(EdgeClient(), "/problems")
    select = re.search(r'<select name="group".*?</select>', html, re.S)
    assert select
    assert f'value="{toi["toi20"].pk}"' in select[0]
    assert "กลุ่ม toi20" in select[0]


# ---- header and speed ----


def test_header_links_the_archive() -> None:
    html = get(EdgeClient(), "/archive")
    assert re.search(r'<a href="/archive" aria-current="page"', html)
    assert "คลังโจทย์" in html


def test_archive_query_count_does_not_grow(
    alice: User, django_assert_max_num_queries: DjangoAssertNumQueries
) -> None:
    for number in range(12):
        group = make_group(f"g{number}")
        sub = make_group(f"s{number}", parent=group)
        solve(alice, make_problem(f"p{number}", sub))
    client = signed_in(alice)
    client.get("/archive")
    with django_assert_max_num_queries(6):
        client.get("/archive")
