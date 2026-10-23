"""The admin dashboard: live counts, quick actions, recent submissions and judge errors."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import User
from core.models import SiteSetting
from judging.models import JudgeJob, Submission, Verdict, Worker
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]


def make_problem(slug: str, *, visible: bool, status: str) -> Problem:
    problem = Problem.objects.create(slug=slug, is_visible=visible)
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64, status=status
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    problem.refresh_from_db()
    return problem


def make_submission(
    problem: Problem, user: User, verdict: str = "", **fields: object
) -> Submission:
    assert problem.current_version is not None
    return Submission.objects.create(
        user=user,
        problem=problem,
        problem_version=problem.current_version,
        language="cpp17",
        source="int main(){}",
        status=Submission.Status.DONE if verdict else Submission.Status.QUEUED,
        verdict=verdict,
        **fields,
    )


@pytest.fixture
def staff() -> EdgeClient:
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True, is_superuser=True))
    return client


def dashboard(client: EdgeClient) -> str:
    response = client.get("/admin/")
    assert response.status_code == 200
    return response.content.decode()


def card(html: str, name: str) -> str:
    pattern = rf'<div class="dash-card" data-card="{name}">.*?<strong>(.*?)</strong>'
    match = re.search(pattern, html, re.S)
    assert match, f"no {name} card"
    return match.group(1).strip()


def test_cards_count_the_queue_workers_problems_and_today(staff: EdgeClient) -> None:
    student = User.objects.create_user(username="student")
    public = make_problem("pub", visible=True, status=ProblemVersion.Status.VALID)
    make_problem("hid", visible=False, status=ProblemVersion.Status.VALID)
    make_problem("val", visible=False, status=ProblemVersion.Status.VALIDATING)
    make_problem("bad", visible=False, status=ProblemVersion.Status.INVALID)
    queued = make_submission(public, student)
    JudgeJob.objects.create(
        kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH, submission=queued
    )
    old = make_submission(public, student, "AC", score=100)
    Submission.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
    Worker.objects.create(worker_id="w1", last_seen_at=timezone.now())
    Worker.objects.create(worker_id="w2", last_seen_at=timezone.now() - timedelta(hours=2))
    html = dashboard(staff)
    assert card(html, "queue") == "1"
    assert card(html, "workers") == "1"
    assert card(html, "public") == "1"
    assert card(html, "hidden") == "3"
    assert card(html, "validating") == "1"
    assert card(html, "invalid") == "1"
    assert card(html, "today") == "1"


def test_grading_state_shows_paused_or_running(staff: EdgeClient) -> None:
    assert 'data-grading="running"' in dashboard(staff)
    setting = SiteSetting.get()
    setting.grading_paused = True
    setting.save()
    assert 'data-grading="paused"' in dashboard(staff)


def test_quick_actions_link_the_admin_pages(staff: EdgeClient) -> None:
    html = dashboard(staff)
    actions = re.search(r'<nav class="quick-actions".*?</nav>', html, re.S)
    assert actions
    for url in ["/admin/grader/import/", "/admin/grader/grading/", "/admin/grader/system/"]:
        assert f'href="{url}"' in actions[0], url


def test_recent_submissions_and_judge_errors(staff: EdgeClient) -> None:
    student = User.objects.create_user(username="student")
    problem = make_problem("pub", visible=True, status=ProblemVersion.Status.VALID)
    submissions = [make_submission(problem, student, "AC", score=100) for _ in range(12)]
    broken = make_submission(problem, student, Verdict.JUDGE_ERROR, judged_at=timezone.now())
    html = dashboard(staff)
    recent = re.search(r'<section class="dash-recent".*?</section>', html, re.S)
    assert recent
    shown = [int(n) for n in re.findall(r'data-submission="(\d+)"', recent[0])]
    assert shown == [broken.pk] + [s.pk for s in reversed(submissions)][:9]
    errors = re.search(r'<section class="dash-errors".*?</section>', html, re.S)
    assert errors and f"#{broken.pk}" in errors[0]


def test_the_old_page_list_and_the_model_list_stay(staff: EdgeClient) -> None:
    html = dashboard(staff)
    assert 'id="grader-pages"' in html
    # The cards come first; Django's list of models moves below them.
    assert html.index('class="dash-cards"') < html.index('class="app-problems')


def test_dashboard_needs_staff() -> None:
    assert EdgeClient().get("/admin/").status_code == 302
