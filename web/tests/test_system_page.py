"""The admin System page: workers, env report, queue, recent judge errors, and the self-test."""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import Client
from django.utils import timezone

from accounts.models import User
from core.models import AuditLog
from judging.models import JudgeJob, Submission, Verdict, Worker
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PAGE = "/admin/grader/system/"
PASSWORD = "a-long-test-password-1"


@pytest.fixture
def staff() -> Client:
    client = EdgeClient()
    client.force_login(User.objects.create_superuser("boss", password=PASSWORD))
    return client


def page(client: Client) -> str:
    response = client.get(PAGE)
    assert response.status_code == 200
    return str(response.content.decode())


def worker_post(path: str, data: dict[str, Any]) -> Any:
    client = Client(headers={"Authorization": "Bearer " + settings.WORKER_TOKEN})
    return client.post(path, json.dumps(data), content_type="application/json")


def claim(worker_id: str = "w1", free_slots: int = 1) -> Any:
    return worker_post("/internal/judge/claim", {"worker_id": worker_id, "free_slots": free_slots})


def make_submission(**fields: Any) -> Submission:
    user, _ = User.objects.get_or_create(username="alice")
    problem, _ = Problem.objects.get_or_create(slug="aplusb", defaults={"is_visible": True})
    version, _ = ProblemVersion.objects.get_or_create(
        problem=problem, dir_name="v001-0123abcd", defaults={"content_hash": "0" * 64}
    )
    return Submission.objects.create(
        user=user, problem=problem, problem_version=version, language="cpp17", source="x", **fields
    )


CASE = {"name": "fork_bomb", "expected": ["TLE"], "verdict": "TLE", "ok": True, "seconds": 3.2,
        "message": ""}  # fmt: skip


def selftest_report(job_id: int, cases: list[dict[str, Any]]) -> Any:
    report = {"kind": "selftest", "cases": cases}
    return worker_post(
        "/internal/judge/report", {"worker_id": "w1", "job_id": job_id, "report": report}
    )


# ---- access ----


def test_the_page_is_for_staff_who_may_see_workers() -> None:
    assert EdgeClient().get(PAGE).status_code == 302
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(PAGE).status_code == 403
    helper.user_permissions.add(Permission.objects.get(codename="view_worker"))
    assert client.get(PAGE).status_code == 200
    # Seeing is not running: the self-test needs the permission to add jobs.
    assert client.post(PAGE, {"action": "selftest"}).status_code == 403
    assert not JudgeJob.objects.exists()


# ---- workers and the env report ----


def test_workers_with_online_state_slots_and_env_report(staff: Client) -> None:
    now = timezone.now()
    Worker.objects.create(
        worker_id="worker-a", slots=2, busy_slots=1, last_seen_at=now,
        env_report={"isolate": "The process isolator 2.7",
                    "warnings": ["judge CPUs 4 and 5 are SMT siblings"]},
    )  # fmt: skip
    Worker.objects.create(worker_id="worker-old", slots=2, last_seen_at=now - timedelta(hours=1))
    html = page(staff)
    assert "worker-a" in html and "worker-old" in html
    assert 'data-worker="worker-a" data-online="yes"' in html
    assert 'data-worker="worker-old" data-online="no"' in html
    assert "The process isolator 2.7" in html
    assert "judge CPUs 4 and 5 are SMT siblings" in html


def test_a_claim_records_how_many_slots_are_busy() -> None:
    Worker.objects.create(worker_id="w1", slots=2)
    make_submission()
    JudgeJob.objects.create(
        kind=JudgeJob.Kind.JUDGE,
        priority=JudgeJob.Priority.FRESH,
        submission=Submission.objects.get(),
    )
    claim(free_slots=2)  # both free before, one taken now
    assert Worker.objects.get().busy_slots == 1
    claim(free_slots=2)  # nothing queued: both free
    assert Worker.objects.get().busy_slots == 0


# ---- queue and errors ----


def test_queue_length_by_kind(staff: Client) -> None:
    for _ in range(3):
        JudgeJob.objects.create(
            kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH,
            submission=make_submission(),
        )  # fmt: skip
    JudgeJob.objects.create(kind=JudgeJob.Kind.SELFTEST, priority=JudgeJob.Priority.BACKGROUND)
    html = page(staff)
    assert 'data-queued-kind="judge">3<' in html
    assert 'data-queued-kind="selftest">1<' in html


def test_the_last_50_judge_errors(staff: Client) -> None:
    now = timezone.now()
    for i in range(52):
        submission = make_submission(
            status=Submission.Status.DONE, verdict=Verdict.JUDGE_ERROR, judged_at=now
        )
        JudgeJob.objects.create(
            kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH, submission=submission,
            status=JudgeJob.Status.FAILED, error=f"isolate broke #{i}",
        )  # fmt: skip
    html = page(staff)
    assert html.count('data-judge-error="') == 50
    assert "isolate broke #51" in html  # newest first
    assert "isolate broke #0<" not in html and "isolate broke #1<" not in html


def test_a_judge_error_result_without_a_failed_job_is_listed(staff: Client) -> None:
    make_submission(status=Submission.Status.DONE, verdict=Verdict.JUDGE_ERROR,
                    judged_at=timezone.now())  # fmt: skip
    assert page(staff).count('data-judge-error="') == 1


# ---- self-test ----


def test_run_self_test_queues_one_job_and_is_audited(staff: Client) -> None:
    assert staff.post(PAGE, {"action": "selftest"}).status_code == 302
    assert staff.post(PAGE, {"action": "selftest"}).status_code == 302  # already waiting
    job = JudgeJob.objects.get()
    assert (job.kind, job.priority, job.status) == ("selftest", JudgeJob.Priority.BACKGROUND,
                                                    "queued")  # fmt: skip
    assert AuditLog.objects.filter(action="system.selftest").count() == 1


def test_self_test_results_are_shown(staff: Client) -> None:
    staff.post(PAGE, {"action": "selftest"})
    job_id = claim().json()["job_id"]
    failing = {**CASE, "name": "read_secrets", "expected": ["AC"], "verdict": "WA", "ok": False,
               "message": "printed /etc/shadow"}  # fmt: skip
    assert selftest_report(job_id, [CASE, failing]).status_code == 204
    html = page(staff)
    assert 'data-selftest="fail"' in html
    assert 'data-case="fork_bomb" data-ok="yes"' in html
    assert 'data-case="read_secrets" data-ok="no"' in html
    assert "printed /etc/shadow" in html


def test_a_passing_self_test(staff: Client) -> None:
    staff.post(PAGE, {"action": "selftest"})
    assert selftest_report(claim().json()["job_id"], [CASE]).status_code == 204
    assert 'data-selftest="pass"' in page(staff)


def test_a_self_test_report_is_only_for_a_self_test_job() -> None:
    make_submission()
    JudgeJob.objects.create(
        kind=JudgeJob.Kind.JUDGE,
        priority=JudgeJob.Priority.FRESH,
        submission=Submission.objects.get(),
    )
    assert selftest_report(claim().json()["job_id"], [CASE]).status_code == 409


def test_long_gone_workers_are_hidden_and_only_online_ones_show_their_report(staff: Client) -> None:
    now = timezone.now()
    Worker.objects.create(worker_id="current", slots=2, last_seen_at=now,
                          env_report={"warnings": ["CURRENT WARNING"]})  # fmt: skip
    Worker.objects.create(worker_id="yesterday", slots=2, last_seen_at=now - timedelta(days=1),
                          env_report={"warnings": ["OLD WARNING"]})  # fmt: skip
    Worker.objects.create(worker_id="replaced", slots=2, last_seen_at=now - timedelta(days=8))
    html = page(staff)
    assert 'data-worker="current"' in html and 'data-worker="yesterday"' in html
    assert "replaced" not in html  # an old container's id, gone for good
    assert "CURRENT WARNING" in html and "OLD WARNING" not in html
