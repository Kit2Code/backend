"""The submission page, its live results fragment (htmx), and the source view."""

import pytest
from django.contrib.staticfiles import finders
from django.test import Client

from accounts.models import User
from judging.models import Submission, TestResult, Verdict
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

SOURCE = 'int main(){ /* <script>alert("x")</script> */ }'


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice")


def make_submission(user: User, slug: str = "aplusb", **fields: object) -> Submission:
    problem = Problem.objects.create(slug=slug, is_visible=True)
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    return Submission.objects.create(
        user=user,
        problem=problem,
        problem_version=version,
        language="cpp17",
        source=SOURCE,
        **fields,
    )


def add_test(submission: Submission, name: str, verdict: str = "AC", **fields: object) -> None:
    values: dict[str, object] = {"outcome": 1.0, "time_ms": 0, "memory_kb": 0, **fields}
    TestResult.objects.create(submission=submission, name=name, verdict=verdict, **values)


def signed_in(user: User) -> Client:
    client = EdgeClient()
    client.force_login(user)
    return client


def page(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


# ---- live updates ----


@pytest.mark.parametrize("status", [Submission.Status.QUEUED, Submission.Status.JUDGING])
def test_a_waiting_submission_polls_its_results(alice: User, status: str) -> None:
    submission = make_submission(alice, status=status)
    html = page(EdgeClient(), f"/submissions/{submission.pk}")
    assert f'hx-get="/submissions/{submission.pk}/results"' in html
    assert 'hx-trigger="every 2s"' in html
    assert 'hx-swap="outerHTML"' in html
    assert "/static/vendor/htmx/htmx.min.js" in html


def test_polling_stops_when_done(alice: User) -> None:
    submission = make_submission(alice, status=Submission.Status.JUDGING)
    url = f"/submissions/{submission.pk}/results"
    assert 'hx-trigger="every 2s"' in page(EdgeClient(), url)
    Submission.objects.filter(pk=submission.pk).update(
        status=Submission.Status.DONE, verdict=Verdict.ACCEPTED, score=100
    )
    fragment = page(EdgeClient(), url)
    assert "hx-get" not in fragment
    assert "hx-trigger" not in fragment
    assert 'id="results"' in fragment
    assert "<html" not in fragment  # only the fragment, for htmx to swap in
    assert "hx-trigger" not in page(EdgeClient(), f"/submissions/{submission.pk}")


def test_tests_so_far_show_while_judging(alice: User) -> None:
    submission = make_submission(alice, status=Submission.Status.JUDGING)
    add_test(submission, "01", time_ms=12)
    html = page(EdgeClient(), f"/submissions/{submission.pk}/results")
    assert "01" in html
    assert "Accepted" in html or "ผ่าน" in html


def test_live_pages_are_not_cached(alice: User) -> None:
    submission = make_submission(alice, status=Submission.Status.JUDGING)
    for url in (f"/submissions/{submission.pk}", f"/submissions/{submission.pk}/results"):
        assert "no-store" in EdgeClient().get(url)["Cache-Control"]


def test_htmx_is_vendored() -> None:
    assert finders.find("vendor/htmx/htmx.min.js")


# ---- the result ----


def test_a_finished_result(alice: User) -> None:
    submission = make_submission(
        alice,
        status=Submission.Status.DONE,
        verdict=Verdict.PARTIAL,
        score=40,
        subtask_scores=[
            {"name": "small", "points": 40, "score": 40},
            {"name": "large", "points": 60, "score": 0},
        ],
        max_time_ms=1234,
        max_memory_kb=2457,
    )
    add_test(submission, "1-01", time_ms=1234, memory_kb=2457)
    add_test(submission, "2-01", "TLE", outcome=0, time_ms=7, memory_kb=1024, message="Too slow")
    html = page(signed_in(alice), f"/submissions/{submission.pk}/results")
    assert "40" in html
    assert "small" in html and "large" in html
    assert "1230 ms" in html  # times rounded to 10 ms
    assert "10 ms" in html
    assert "2.4 MB" in html  # memory rounded to 0.1 MB
    assert "1.0 MB" in html
    assert "Too slow" in html
    assert "1234" not in html  # the exact figures are not shown


def test_compile_errors_show_the_log(alice: User) -> None:
    submission = make_submission(
        alice,
        status=Submission.Status.DONE,
        verdict=Verdict.COMPILE_ERROR,
        score=0,
        compile_log="a.cpp:1:1: error: <expected> ';'",
    )
    html = page(signed_in(alice), f"/submissions/{submission.pk}")
    assert "a.cpp:1:1: error: &lt;expected&gt; &#x27;;&#x27;" in html


def test_the_compile_log_is_shown_only_for_compile_errors(alice: User) -> None:
    submission = make_submission(
        alice,
        status=Submission.Status.DONE,
        verdict=Verdict.ACCEPTED,
        score=100,
        compile_log="a.cpp:1:1: warning: unused variable",
    )
    assert "unused variable" not in page(EdgeClient(), f"/submissions/{submission.pk}")


# ---- who sees what ----


def test_the_author_sees_the_code(alice: User) -> None:
    submission = make_submission(alice)
    client = signed_in(alice)
    html = page(client, f"/submissions/{submission.pk}")
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "<script>alert" not in html
    response = client.get(f"/submissions/{submission.pk}/source")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert response.content.decode() == SOURCE
    assert "no-store" in response["Cache-Control"]


@pytest.mark.parametrize("who", ["someone else", "signed out"])
def test_others_never_see_the_code(alice: User, who: str) -> None:
    submission = make_submission(alice)
    client = (
        signed_in(User.objects.create_user(username="bob"))
        if who == "someone else"
        else EdgeClient()
    )
    html = page(client, f"/submissions/{submission.pk}")
    assert "alert" not in html
    assert f"/submissions/{submission.pk}/source" not in html
    assert client.get(f"/submissions/{submission.pk}/source").status_code == 404


def test_hidden_problems_hide_their_submissions(alice: User) -> None:
    submission = make_submission(alice)
    Problem.objects.update(is_visible=False)
    client = signed_in(alice)
    for suffix in ("", "/results", "/source"):
        assert client.get(f"/submissions/{submission.pk}{suffix}").status_code == 404


def test_unknown_submission_is_404() -> None:
    assert EdgeClient().get("/submissions/999999").status_code == 404
    assert EdgeClient().get("/submissions/999999/results").status_code == 404


# ---- the compiler output is the author's only (it quotes their source lines) ----

COMPILE_LOG = "a.cpp:3:5: error: 'secret_idea' was not declared"


def compile_error(user: User) -> Submission:
    return make_submission(
        user,
        status=Submission.Status.DONE,
        verdict=Verdict.COMPILE_ERROR,
        score=0,
        compile_log=COMPILE_LOG,
    )


@pytest.mark.parametrize("suffix", ["", "/results"])
def test_others_see_the_compile_error_but_not_the_log(alice: User, suffix: str) -> None:
    submission = compile_error(alice)
    bob = User.objects.create_user(username="bob")
    for client in (EdgeClient(), signed_in(bob)):
        html = page(client, f"/submissions/{submission.pk}{suffix}")
        assert "คอมไพล์ไม่ผ่าน" in html
        assert "secret_idea" not in html


@pytest.mark.parametrize("suffix", ["", "/results"])
def test_the_author_sees_the_compile_log(alice: User, suffix: str) -> None:
    submission = compile_error(alice)
    html = page(signed_in(alice), f"/submissions/{submission.pk}{suffix}")
    assert "secret_idea" in html


def test_htmx_adds_no_inline_styles(alice: User) -> None:
    # The edge's Content-Security-Policy allows no inline styles (deploy/edge/Caddyfile).
    html = page(EdgeClient(), f"/submissions/{make_submission(alice).pk}")
    assert '"includeIndicatorStyles": false' in html
