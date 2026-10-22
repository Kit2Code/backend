"""web-admin's internal judge API (/internal/*), as the worker uses it.

The worker polls: it claims one job at a time, sends heartbeats and progress, and posts the result
(or a failure). Every request is JSON checked against grader_common.api; the zone middleware has
already made sure it came straight from the worker, with its token.
"""

import json
import threading
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core.models import PauseWindow, SiteSetting
from judging.models import JudgeJob, Submission, TestResult, UserProblemBest, Worker
from problems.models import Problem, ProblemVersion
from tests.conftest import use_role

pytestmark = pytest.mark.django_db

ENDPOINTS = [
    "/internal/worker/hello",
    "/internal/judge/claim",
    "/internal/judge/heartbeat",
    "/internal/judge/progress",
    "/internal/judge/complete",
    "/internal/judge/fail",
]


@pytest.fixture(autouse=True)
def _admin(settings: SettingsWrapper) -> None:
    use_role(settings, "admin")


def worker_client() -> Client:
    """The worker: no zone header, its bearer token."""
    return Client(headers={"Authorization": "Bearer " + settings.WORKER_TOKEN})


def post(path: str, data: dict[str, Any], client: Client | None = None) -> Any:
    return (client or worker_client()).post(path, json.dumps(data), content_type="application/json")


def make_submission(
    username: str = "alice", priority: int = JudgeJob.Priority.FRESH, source: str = "int main(){}"
) -> Submission:
    user, _ = User.objects.get_or_create(username=username)
    problem, _ = Problem.objects.get_or_create(slug="aplusb", defaults={"is_visible": True})
    version, _ = ProblemVersion.objects.get_or_create(
        problem=problem, dir_name="v001-0123abcd", defaults={"content_hash": "0" * 64}
    )
    submission = Submission.objects.create(
        user=user, problem=problem, problem_version=version, language="cpp17", source=source
    )
    JudgeJob.objects.create(kind=JudgeJob.Kind.JUDGE, priority=priority, submission=submission)
    return submission


def claim(worker_id: str = "w1", client: Client | None = None) -> Any:
    return post("/internal/judge/claim", {"worker_id": worker_id, "free_slots": 1}, client)


def result(verdict: str = "AC", score: float = 100, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "verdict": verdict,
        "score": score,
        "max_score": 100,
        "compile_log": "",
        "tests": [
            {
                "name": "01",
                "verdict": verdict,
                "outcome": 1.0,
                "time": 0.0123,
                "wall_time": 0.02,
                "memory_kb": 2048,
                "message": "ok",
            },
        ],  # fmt: skip
        "subtasks": [{"name": "1", "points": 100, "score": score}],
        "max_time": 0.0123,
        "max_memory_kb": 2048,
    }
    data.update(extra)
    return data


def complete(job_id: int, worker_id: str = "w1", **result_extra: Any) -> Any:
    return post(
        "/internal/judge/complete",
        {"worker_id": worker_id, "job_id": job_id, "result": result(**result_extra)},
    )


# ---- access ----


@pytest.mark.parametrize("path", ENDPOINTS)
def test_without_the_token_it_is_404(path: str) -> None:
    assert Client().post(path, "{}", content_type="application/json").status_code == 404
    bad = Client(headers={"Authorization": "Bearer wrong"})
    assert bad.post(path, "{}", content_type="application/json").status_code == 404


@pytest.mark.parametrize("path", ENDPOINTS)
def test_through_the_edge_it_is_404(path: str) -> None:
    client = Client(
        headers={"Authorization": "Bearer " + settings.WORKER_TOKEN, "X-Edge-Zone": "admin"}
    )
    assert client.post(path, "{}", content_type="application/json").status_code == 404


@pytest.mark.parametrize("path", ENDPOINTS)
def test_only_post(path: str) -> None:
    assert worker_client().get(path).status_code == 405


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("body", ["not json", "[]", '{"unexpected": 1}', ""])
def test_bad_bodies_are_400(path: str, body: str) -> None:
    response = worker_client().post(path, body, content_type="application/json")
    assert response.status_code == 400


def test_public_role_has_no_internal_api(settings: SettingsWrapper) -> None:
    use_role(settings, "public")
    for path in ENDPOINTS:
        assert claim(client=worker_client()).status_code == 404
        assert worker_client().post(path, "{}", content_type="application/json").status_code == 404


# ---- hello ----


def test_hello_records_the_worker() -> None:
    response = post(
        "/internal/worker/hello",
        {"worker_id": "w1", "slots": 2, "env_report": {"isolate": "2.7"}},
    )
    assert response.status_code == 204
    worker = Worker.objects.get(worker_id="w1")
    assert (worker.slots, worker.env_report) == (2, {"isolate": "2.7"})
    first_seen = worker.last_seen_at
    post("/internal/worker/hello", {"worker_id": "w1", "slots": 2})
    worker.refresh_from_db()
    assert worker.last_seen_at is not None and first_seen is not None
    assert worker.last_seen_at >= first_seen
    assert Worker.objects.count() == 1


# ---- claim ----


def test_claim_with_nothing_queued_is_204() -> None:
    assert claim().status_code == 204


def test_claim_hands_out_the_job_with_everything_needed() -> None:
    setting = SiteSetting.get()
    setting.time_scale = 1.5
    setting.save()
    submission = make_submission(source="#include <cstdio>\nint main(){}")
    response = claim()
    assert response.status_code == 200
    job = response.json()
    assert job == {
        "job_id": JudgeJob.objects.get().pk,
        "kind": "judge",
        "problem_path": "aplusb/v001-0123abcd",
        "language": "cpp17",
        "source": "#include <cstdio>\nint main(){}",
        "time_scale": 1.5,
    }
    row = JudgeJob.objects.get()
    assert (row.status, row.worker_id, row.attempts) == ("running", "w1", 1)
    assert row.lease_until is not None
    lease = (row.lease_until - timezone.now()).total_seconds()
    assert 50 < lease <= 60
    submission.refresh_from_db()
    assert submission.status == Submission.Status.JUDGING
    assert claim().status_code == 204  # the only job is taken


def test_higher_priority_first_then_oldest() -> None:
    rejudge = make_submission("a", priority=JudgeJob.Priority.REJUDGE)
    fresh_old = make_submission("b")
    fresh_new = make_submission("c")
    order = [claim().json()["job_id"] for _ in range(3)]
    assert order == [
        fresh_old.judgejob_set.get().pk,
        fresh_new.judgejob_set.get().pk,
        rejudge.judgejob_set.get().pk,
    ]


def test_nothing_is_handed_out_while_grading_is_paused() -> None:
    make_submission()
    setting = SiteSetting.get()
    setting.grading_paused = True
    setting.save()
    assert claim().status_code == 204
    setting.grading_paused = False
    setting.save()
    assert claim().status_code == 200


def test_nothing_is_handed_out_inside_a_pause_window() -> None:
    make_submission()
    now = timezone.now()
    window = PauseWindow.objects.create(
        starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(minutes=5)
    )
    assert claim().status_code == 204
    window.ends_at = now - timedelta(minutes=1)
    window.starts_at = now - timedelta(minutes=10)
    window.save()
    assert claim().status_code == 200


def test_an_expired_lease_is_handed_out_again() -> None:
    make_submission()
    first = claim("w1").json()["job_id"]
    JudgeJob.objects.filter(pk=first).update(lease_until=timezone.now() - timedelta(seconds=1))
    response = claim("w2")
    assert response.status_code == 200
    assert response.json()["job_id"] == first
    job = JudgeJob.objects.get()
    assert (job.worker_id, job.attempts) == ("w2", 2)


def test_after_three_tries_an_expired_job_is_a_judge_error() -> None:
    submission = make_submission()
    for _ in range(3):
        job_id = claim().json()["job_id"]
        JudgeJob.objects.filter(pk=job_id).update(lease_until=timezone.now() - timedelta(seconds=1))
    assert claim().status_code == 204
    job = JudgeJob.objects.get()
    assert job.status == JudgeJob.Status.FAILED
    submission.refresh_from_db()
    assert (submission.status, submission.verdict) == ("done", "JE")


@pytest.mark.django_db(transaction=True)
def test_two_workers_never_get_the_same_job() -> None:
    for name in ("a", "b", "c", "d"):
        make_submission(name)
    claimed: list[int] = []
    barrier = threading.Barrier(4)

    def worker(worker_id: str) -> None:
        try:
            client = worker_client()
            barrier.wait()
            response = claim(worker_id, client)
            if response.status_code == 200:
                claimed.append(response.json()["job_id"])
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == 4
    assert len(set(claimed)) == 4


# ---- heartbeat and progress ----


def test_heartbeat_extends_the_lease_and_reports_lost_jobs() -> None:
    make_submission("a")
    make_submission("b")
    mine = claim("w1").json()["job_id"]
    theirs = claim("w2").json()["job_id"]
    JudgeJob.objects.filter(pk=mine).update(lease_until=timezone.now() + timedelta(seconds=5))
    response = post("/internal/judge/heartbeat", {"worker_id": "w1", "job_ids": [mine, theirs]})
    assert response.status_code == 200
    assert response.json() == {"lost": [theirs]}
    lease = JudgeJob.objects.get(pk=mine).lease_until
    assert lease is not None and (lease - timezone.now()).total_seconds() > 50


def test_progress_stores_the_tests_so_far() -> None:
    submission = make_submission()
    job_id = claim().json()["job_id"]
    tests = result()["tests"]
    response = post(
        "/internal/judge/progress", {"worker_id": "w1", "job_id": job_id, "tests": tests}
    )
    assert response.status_code == 204
    post("/internal/judge/progress", {"worker_id": "w1", "job_id": job_id, "tests": tests})
    (row,) = TestResult.objects.filter(submission=submission)
    assert (row.name, row.verdict, row.time_ms, row.memory_kb) == ("01", "AC", 12, 2048)


def test_progress_from_another_worker_is_409() -> None:
    make_submission()
    job_id = claim("w1").json()["job_id"]
    response = post("/internal/judge/progress", {"worker_id": "w2", "job_id": job_id, "tests": []})
    assert response.status_code == 409


# ---- complete ----


def test_complete_stores_the_result() -> None:
    submission = make_submission()
    job_id = claim().json()["job_id"]
    response = complete(job_id, verdict="PA", score=40, compile_log="warning: x")
    assert response.status_code == 204
    submission.refresh_from_db()
    assert submission.status == "done"
    assert (submission.verdict, submission.score) == ("PA", 40)
    assert submission.subtask_scores == [{"name": "1", "points": 100, "score": 40}]
    assert (submission.max_time_ms, submission.max_memory_kb) == (12, 2048)
    assert submission.compile_log == "warning: x"
    assert submission.judged_at is not None
    assert TestResult.objects.filter(submission=submission).count() == 1
    assert JudgeJob.objects.get().status == JudgeJob.Status.DONE


def test_complete_twice_or_by_another_worker_is_409() -> None:
    make_submission()
    job_id = claim("w1").json()["job_id"]
    assert complete(job_id, worker_id="w2").status_code == 409
    assert complete(job_id).status_code == 204
    assert complete(job_id).status_code == 409


def test_a_huge_compile_log_is_refused() -> None:
    make_submission()
    job_id = claim().json()["job_id"]
    assert complete(job_id, compile_log="x" * (64 * 1024 + 1)).status_code == 400


def test_best_scores_for_the_ranking() -> None:
    first = make_submission()
    complete(claim().json()["job_id"], verdict="PA", score=40)
    best = UserProblemBest.objects.get()
    assert (best.best_score, best.submission_id) == (40, first.pk)
    assert best.reached_at == first.created_at

    # A lower or equal score later changes nothing (the time it was first reached counts).
    make_submission()
    complete(claim().json()["job_id"], verdict="PA", score=40)
    best.refresh_from_db()
    assert (best.best_score, best.submission_id) == (40, first.pk)

    better = make_submission()
    complete(claim().json()["job_id"], verdict="AC", score=100)
    best.refresh_from_db()
    assert (best.best_score, best.submission_id) == (100, better.pk)
    assert best.reached_at == better.created_at
    assert UserProblemBest.objects.count() == 1


# ---- fail ----


def test_failure_puts_the_job_back_until_the_third_try() -> None:
    submission = make_submission()
    for attempt in (1, 2):
        job_id = claim().json()["job_id"]
        response = post(
            "/internal/judge/fail", {"worker_id": "w1", "job_id": job_id, "error": "disk full"}
        )
        assert response.status_code == 204
        job = JudgeJob.objects.get()
        assert (job.status, job.attempts, job.worker_id) == ("queued", attempt, "")
    job_id = claim().json()["job_id"]
    post("/internal/judge/fail", {"worker_id": "w1", "job_id": job_id, "error": "disk full"})
    job = JudgeJob.objects.get()
    assert job.status == JudgeJob.Status.FAILED
    assert job.error == "disk full"
    submission.refresh_from_db()
    assert (submission.status, submission.verdict) == ("done", "JE")


# ---- a broken setting or a bad result must not burn the queue or poison the ranking ----


def test_a_claim_that_cannot_be_answered_leaves_the_job_queued() -> None:
    submission = make_submission()
    SiteSetting.objects.update_or_create(pk=1, defaults={"time_scale": 0})
    client = Client(
        headers={"Authorization": "Bearer " + settings.WORKER_TOKEN},
        raise_request_exception=False,
    )
    assert claim(client=client).status_code == 500
    job = JudgeJob.objects.get()
    assert (job.status, job.attempts, job.worker_id) == (JudgeJob.Status.QUEUED, 0, "")
    submission.refresh_from_db()
    assert submission.status == Submission.Status.QUEUED


@pytest.mark.parametrize(
    "bad",
    [
        {"score": float("nan")},
        {"score": float("inf")},
        {"max_score": float("inf")},
        {"max_time": float("inf")},
        {"score": -1},
        {"score": 150},  # more than max_score (100)
        {"max_memory_kb": 2**40},
        {"subtasks": [{"name": "1", "points": 100, "score": -5}]},
    ],
)
def test_impossible_numbers_in_a_result_are_400(bad: dict[str, Any]) -> None:
    submission = make_submission()
    job_id = claim().json()["job_id"]
    data = {"worker_id": "w1", "job_id": job_id, "result": result(**bad)}
    # json.dumps writes NaN/Infinity, as a careless or hostile worker might.
    assert post("/internal/judge/complete", data).status_code == 400
    submission.refresh_from_db()
    assert submission.status == Submission.Status.JUDGING


def test_a_rejudge_that_scores_lower_lowers_the_best_score() -> None:
    submission = make_submission()
    assert complete(claim().json()["job_id"], score=100).status_code == 204
    assert UserProblemBest.objects.get().best_score == 100
    JudgeJob.objects.create(
        kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.REJUDGE, submission=submission
    )
    assert complete(claim().json()["job_id"], verdict="PA", score=40).status_code == 204
    best = UserProblemBest.objects.get()
    assert (best.best_score, best.submission_id) == (40, submission.pk)


def test_a_job_with_nothing_to_work_on_is_given_up_not_handed_out() -> None:
    broken = JudgeJob.objects.create(kind=JudgeJob.Kind.VALIDATE, priority=JudgeJob.Priority.FRESH)
    make_submission()
    assert claim().status_code == 204  # this round only gave up the broken job
    broken.refresh_from_db()
    assert broken.status == JudgeJob.Status.FAILED
    assert claim().json()["kind"] == "judge"  # the queue moves on
