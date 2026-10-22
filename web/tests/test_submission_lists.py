"""My submissions and all submissions: no code, filters, pages."""

import re
from collections.abc import Callable

import pytest
from django.test import Client

from accounts.models import User
from judging.models import Submission, Verdict
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

SECRET_CODE = "int main(){ return 42; } // secret-marker"


def make_problem(slug: str, visible: bool = True) -> Problem:
    problem = Problem.objects.create(slug=slug, is_visible=visible)
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    problem.refresh_from_db()
    return problem


def make_submission(
    user: User, problem: Problem, verdict: str = "", score: float | None = None
) -> Submission:
    assert problem.current_version is not None
    return Submission.objects.create(
        user=user,
        problem=problem,
        problem_version=problem.current_version,
        language="cpp17",
        source=SECRET_CODE,
        status=Submission.Status.DONE if verdict else Submission.Status.QUEUED,
        verdict=verdict,
        score=score,
    )


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice")


@pytest.fixture
def bob() -> User:
    return User.objects.create_user(username="bob")


@pytest.fixture
def aplusb() -> Problem:
    return make_problem("aplusb")


def listed(client: Client, url: str) -> list[int]:
    """The submission ids linked from a list page, in order."""
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "secret-marker" not in html  # lists never show code
    return [int(n) for n in re.findall(r'href="/submissions/(\d+)"', html)]


def signed_in(user: User) -> Client:
    client = EdgeClient()
    client.force_login(user)
    return client


# ---- all submissions ----


def test_all_submissions_newest_first(alice: User, bob: User, aplusb: Problem) -> None:
    first = make_submission(alice, aplusb, "AC", 100)
    second = make_submission(bob, aplusb, "WA", 0)
    third = make_submission(alice, aplusb)
    assert listed(EdgeClient(), "/submissions") == [third.pk, second.pk, first.pk]


def test_the_list_shows_who_what_and_how(alice: User, aplusb: Problem) -> None:
    make_submission(alice, aplusb, "PA", 40)
    html = EdgeClient().get("/submissions").content.decode()
    for text in ("alice", "aplusb", "C++17", "40"):
        assert text in html
    assert "Partially correct" in html or "ถูกบางส่วน" in html


def test_hidden_problems_are_not_listed(alice: User, aplusb: Problem) -> None:
    hidden = make_problem("secret", visible=False)
    shown = make_submission(alice, aplusb)
    make_submission(alice, hidden)
    assert listed(EdgeClient(), "/submissions") == [shown.pk]
    assert listed(signed_in(alice), "/submissions/mine") == [shown.pk]
    assert "secret" not in EdgeClient().get("/submissions").content.decode()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("problem=aplusb", ["a1", "b1"]),
        ("problem=other", ["a2"]),
        ("user=alice", ["a1", "a2"]),
        ("user=bob", ["b1"]),
        ("verdict=AC", ["a1"]),
        ("verdict=WA", ["b1", "a2"]),
        ("user=alice&verdict=WA", ["a2"]),
        ("user=nobody", []),
        ("verdict=XX", []),  # not a verdict: the form refuses it, nothing is listed
        ("problem=", ["a1", "b1", "a2"]),  # empty filters are ignored
    ],
)
def test_filters(alice: User, bob: User, aplusb: Problem, query: str, expected: list[str]) -> None:
    other = make_problem("other")
    made = {
        "a1": make_submission(alice, aplusb, "AC", 100),
        "b1": make_submission(bob, aplusb, "WA", 0),
        "a2": make_submission(alice, other, "WA", 0),
    }
    wanted = sorted((made[name].pk for name in expected), reverse=True)
    assert listed(EdgeClient(), f"/submissions?{query}") == wanted


def test_pages_of_50(alice: User, aplusb: Problem) -> None:
    made = [make_submission(alice, aplusb) for _ in range(51)]
    assert len(listed(EdgeClient(), "/submissions")) == 50
    assert listed(EdgeClient(), "/submissions?page=2") == [made[0].pk]
    assert len(listed(EdgeClient(), "/submissions?page=nonsense")) == 50
    html = EdgeClient().get("/submissions?user=alice").content.decode()
    assert 'href="?user=alice&amp;page=2"' in html  # page links keep the filters


def test_query_count_does_not_grow_with_rows(
    alice: User, aplusb: Problem, django_assert_max_num_queries: Callable[..., object]
) -> None:
    for _ in range(20):
        make_submission(alice, aplusb, "AC", 100)
    with django_assert_max_num_queries(8):  # type: ignore[attr-defined]
        EdgeClient().get("/submissions")


# ---- my submissions ----


def test_my_submissions_needs_sign_in() -> None:
    response = EdgeClient().get("/submissions/mine")
    assert response.status_code == 302
    assert response["Location"] == "/auth/google/start?next=/submissions/mine"


def test_my_submissions_are_only_mine(alice: User, bob: User, aplusb: Problem) -> None:
    mine = [make_submission(alice, aplusb), make_submission(alice, aplusb, "AC", 100)]
    make_submission(bob, aplusb)
    assert listed(signed_in(alice), "/submissions/mine") == [mine[1].pk, mine[0].pk]


def test_navigation_links(alice: User) -> None:
    assert 'href="/submissions"' in EdgeClient().get("/").content.decode()
    html = signed_in(alice).get("/").content.decode()
    assert 'href="/submissions/mine"' in html


# ---- code stays private ----


def test_another_users_code_is_404(alice: User, bob: User, aplusb: Problem) -> None:
    submission = make_submission(alice, aplusb)
    assert signed_in(bob).get(f"/submissions/{submission.pk}/source").status_code == 404
    assert (
        "secret-marker" not in signed_in(bob).get(f"/submissions/{submission.pk}").content.decode()
    )
    assert signed_in(alice).get(f"/submissions/{submission.pk}/source").status_code == 200


def test_submission_verdict_choices_cover_the_filter() -> None:
    assert {"AC", "PA", "WA", "TLE", "MLE", "RE", "OLE", "CE", "JE"} == set(Verdict.values)
