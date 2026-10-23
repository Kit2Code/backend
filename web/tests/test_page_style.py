"""The restyled public pages: limits as badges, sample cards, verdict colours, ranking podium.

The structure older tests rely on (the results table, the submit form's fields) is tested there.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from judging.models import Submission, UserProblemBest
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "problems"


def make_problem(slug: str = "aplusb") -> Problem:
    problem = Problem.objects.create(
        slug=slug, is_visible=True, max_score=100, time_limit_ms=1000, memory_limit_mb=256
    )
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    problem.refresh_from_db()
    return problem


def make_submission(user: User, problem: Problem, verdict: str, score: float) -> Submission:
    assert problem.current_version is not None
    return Submission.objects.create(
        user=user,
        problem=problem,
        problem_version=problem.current_version,
        language="cpp17",
        source="int main(){}",
        status=Submission.Status.DONE,
        verdict=verdict,
        score=score,
    )


def signed_in(user: User) -> EdgeClient:
    client = EdgeClient()
    client.force_login(user)
    return client


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice", display_name="Alice A.")


def test_submission_verdict_has_a_colour_class(alice: User) -> None:
    problem = make_problem()
    accepted = make_submission(alice, problem, "AC", 100)
    wrong = make_submission(alice, problem, "WA", 0)
    html = signed_in(alice).get(f"/submissions/{accepted.pk}").content.decode()
    assert re.search(r'<strong class="verdict verdict-ac">', html)
    html = signed_in(alice).get(f"/submissions/{wrong.pk}").content.decode()
    assert re.search(r'<strong class="verdict verdict-wa">', html)


def test_submission_lists_show_verdict_badges(alice: User) -> None:
    problem = make_problem()
    make_submission(alice, problem, "AC", 100)
    make_submission(alice, problem, "TLE", 0)
    html = EdgeClient().get("/submissions").content.decode()
    assert re.search(r'<span class="verdict verdict-ac">', html)
    assert re.search(r'<span class="verdict verdict-tle">', html)


def test_ranking_marks_the_top_three_and_shows_avatars() -> None:
    problem = make_problem()
    for number, score in enumerate([90, 80, 70, 60]):
        user = User.objects.create_user(username=f"user{number}")
        UserProblemBest.objects.create(
            user=user,
            problem=problem,
            best_score=score,
            reached_at=timezone.now() - timedelta(minutes=number),
        )
    html = EdgeClient().get("/ranking").content.decode()
    assert re.findall(r'<tr class="podium-(\d)">', html) == ["1", "2", "3"]
    assert len(re.findall(r'<span class="avatar"', html)) == 4


def test_pages_use_no_inline_styles(alice: User) -> None:
    problem = make_problem()
    submission = make_submission(alice, problem, "AC", 100)
    client = signed_in(alice)
    for path in [
        "/",
        "/problems",
        "/archive",
        "/ranking",
        "/submissions",
        "/submissions/mine",
        f"/submissions/{submission.pk}",
        "/privacy",
        "/users/alice",
    ]:
        html = client.get(path).content.decode()
        assert " style=" not in html, path
        assert "<style" not in html, path


def test_problem_page_shows_limits_as_badges_and_samples_as_cards(
    tmp_path: Path, settings: SettingsWrapper
) -> None:
    settings.GRADER_PROBLEMS_DIR = tmp_path
    call_command("import_problem", str(FIXTURES / "aplusb"), "--visible", verbosity=0)
    html = EdgeClient().get("/problems/aplusb").content.decode()
    badges = re.findall(r'<span class="badge">(.*?)</span>', html, re.S)
    assert any("1000" in badge for badge in badges)
    assert any("256 MB" in badge for badge in badges)
    assert re.search(r'<div class="sample">', html)
    assert re.search(r'<a href="/problems/aplusb/submit" role="button"', html)
