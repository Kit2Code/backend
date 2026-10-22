"""web-admin's internal judge API: the worker's only way to get work and report results.

Only the admin role routes these URLs, and core.middleware lets a request through only without
a zone header (it didn't come through the edge) and with the worker's bearer token. Bodies are
checked with the shared pydantic models in grader_common.api.

The queue is the JudgeJob table. A claimed job is "leased" to one worker for LEASE; heartbeats
extend it. If a worker disappears, the lease runs out and the next claim puts the job back in the
queue; after MAX_ATTEMPTS tries it becomes a judge error instead. See docs/ARCHITECTURE.md.
"""

from collections.abc import Callable
from datetime import timedelta
from functools import wraps

from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from pydantic import BaseModel, ValidationError

from core.models import PauseWindow, SiteSetting
from grader_common import api
from judging.models import (
    MAX_COMPILE_LOG_BYTES,
    JudgeJob,
    Submission,
    TestResult,
    UserProblemBest,
    Verdict,
    Worker,
)

LEASE = timedelta(seconds=60)
MAX_ATTEMPTS = 3


def _endpoint[Message: BaseModel](
    model: type[Message],
) -> Callable[[Callable[[Message], HttpResponse]], Callable[[HttpRequest], HttpResponse]]:
    """Make a view that takes the request body as `model` (400 if it doesn't fit).

    CSRF protection is off here on purpose: the worker isn't a browser, and the bearer token
    (checked by the zone middleware) is what proves who is calling.
    """

    def decorate(
        handler: Callable[[Message], HttpResponse],
    ) -> Callable[[HttpRequest], HttpResponse]:
        @csrf_exempt
        @require_POST
        @wraps(handler)
        def view(request: HttpRequest) -> HttpResponse:
            try:
                message = model.model_validate_json(request.body)
            except ValidationError:
                return HttpResponse(status=400)
            return handler(message)

        return view

    return decorate


def _no_content() -> HttpResponse:
    return HttpResponse(status=204)


def _not_yours() -> HttpResponse:
    """The job isn't (or is no longer) leased to this worker: it must drop it."""
    return HttpResponse(status=409)


def grading_paused() -> bool:
    """True while an admin has paused grading, or during a pause window (a contest day)."""
    now = timezone.now()
    if SiteSetting.get().grading_paused:
        return True
    return PauseWindow.objects.filter(starts_at__lte=now, ends_at__gt=now).exists()


def _give_up(job: JudgeJob, error: str) -> None:
    """The job won't be tried again: its submission becomes a judge error."""
    job.status = JudgeJob.Status.FAILED
    job.error = error
    job.lease_until = None
    job.save()
    if job.submission_id is not None:
        Submission.objects.filter(pk=job.submission_id).update(
            status=Submission.Status.DONE, verdict=Verdict.JUDGE_ERROR, judged_at=timezone.now()
        )


def _requeue(job: JudgeJob) -> None:
    job.status = JudgeJob.Status.QUEUED
    job.worker_id = ""
    job.lease_until = None
    job.save()


def _release_expired_leases() -> None:
    """Running jobs whose worker stopped sending heartbeats: queue them again (or give up)."""
    expired = JudgeJob.objects.select_for_update(skip_locked=True).filter(
        status=JudgeJob.Status.RUNNING, lease_until__lt=timezone.now()
    )
    for job in expired:
        if job.attempts >= MAX_ATTEMPTS:
            _give_up(job, f"the worker stopped answering, {job.attempts} times")
        else:
            _requeue(job)


def _held_job(worker_id: str, job_id: int) -> JudgeJob | None:
    """The job, locked, if it is running and leased to this worker."""
    return (
        JudgeJob.objects.select_for_update(of=("self",))
        .filter(pk=job_id, status=JudgeJob.Status.RUNNING, worker_id=worker_id)
        .select_related("submission")
        .first()
    )


@_endpoint(api.WorkerHello)
def hello(message: api.WorkerHello) -> HttpResponse:
    Worker.objects.update_or_create(
        worker_id=message.worker_id,
        defaults={
            "slots": message.slots,
            "env_report": message.env_report,
            "last_seen_at": timezone.now(),
        },
    )
    return _no_content()


@_endpoint(api.ClaimRequest)
def claim(message: api.ClaimRequest) -> HttpResponse:
    """Hand out the most urgent queued job, or 204 if there is none (or grading is paused)."""
    Worker.objects.filter(worker_id=message.worker_id).update(last_seen_at=timezone.now())
    if grading_paused():
        return _no_content()
    with transaction.atomic():
        _release_expired_leases()
        # skip_locked: two workers claiming at the same moment each skip the row the other has
        # locked, so they never get the same job and never wait for each other.
        job = (
            JudgeJob.objects.select_for_update(skip_locked=True)
            .filter(status=JudgeJob.Status.QUEUED, kind=JudgeJob.Kind.JUDGE)
            .order_by("-priority", "created_at")
            .first()
        )
        if job is None or job.submission_id is None:
            return _no_content()
        job.status = JudgeJob.Status.RUNNING
        job.worker_id = message.worker_id
        job.attempts += 1
        job.lease_until = timezone.now() + LEASE
        job.save()
        submission = Submission.objects.select_related("problem", "problem_version").get(
            pk=job.submission_id
        )
        submission.status = Submission.Status.JUDGING
        submission.save(update_fields=["status"])
        # Built inside the transaction: if the job can't be described (say, a bad time_scale
        # setting), the error rolls the claim back, so the job stays queued and keeps its tries.
        payload = api.JudgeJob(
            job_id=job.pk,
            kind="judge",
            problem_path=f"{submission.problem.slug}/{submission.problem_version.dir_name}",
            language=submission.language,
            source=submission.source,
            time_scale=SiteSetting.get().time_scale,
        )
    return JsonResponse(payload.model_dump(mode="json"))


@_endpoint(api.Heartbeat)
def heartbeat(message: api.Heartbeat) -> HttpResponse:
    """Extend the leases of the jobs this worker still holds; report the ones it lost."""
    with transaction.atomic():
        held = JudgeJob.objects.select_for_update().filter(
            pk__in=message.job_ids, status=JudgeJob.Status.RUNNING, worker_id=message.worker_id
        )
        kept = set(held.values_list("pk", flat=True))
        held.update(lease_until=timezone.now() + LEASE)
    Worker.objects.filter(worker_id=message.worker_id).update(last_seen_at=timezone.now())
    lost = [job_id for job_id in message.job_ids if job_id not in kept]
    return JsonResponse(api.HeartbeatReply(lost=lost).model_dump(mode="json"))


def _test_row(submission: Submission, test: api.TestResult) -> TestResult:
    return TestResult(
        submission=submission,
        name=test.name,
        verdict=test.verdict,
        outcome=test.outcome,
        time_ms=round(test.time * 1000),
        memory_kb=test.memory_kb,
        message=test.message,
    )


def _store_tests(submission: Submission, tests: list[api.TestResult]) -> None:
    """Save these test results, replacing earlier rows with the same test names."""
    TestResult.objects.filter(submission=submission, name__in=[t.name for t in tests]).delete()
    TestResult.objects.bulk_create([_test_row(submission, test) for test in tests])


@_endpoint(api.Progress)
def progress(message: api.Progress) -> HttpResponse:
    with transaction.atomic():
        job = _held_job(message.worker_id, message.job_id)
        if job is None or job.submission is None:
            return _not_yours()
        _store_tests(job.submission, message.tests)
    return _no_content()


def _cap_utf8(text: str, limit: int) -> str:
    """At most `limit` bytes of UTF-8, cut at a character boundary."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _update_best(submission: Submission, score: float) -> None:
    """Keep the user's best score on the problem, and when they first reached it."""
    lookup = {"user_id": submission.user_id, "problem_id": submission.problem_id}
    best = UserProblemBest.objects.select_for_update().filter(**lookup).first()
    if best is None:
        try:
            with transaction.atomic():  # a savepoint: another result may create the row first
                UserProblemBest.objects.create(
                    **lookup,
                    best_score=score,
                    reached_at=submission.created_at,
                    submission=submission,
                )
            return
        except IntegrityError:
            best = UserProblemBest.objects.select_for_update().get(**lookup)
    if score > best.best_score:
        best.best_score = score
        best.reached_at = submission.created_at
        best.submission = submission
        best.save()


@_endpoint(api.Complete)
def complete(message: api.Complete) -> HttpResponse:
    result = message.result
    with transaction.atomic():
        job = _held_job(message.worker_id, message.job_id)
        if job is None or job.submission is None:
            return _not_yours()
        submission = job.submission
        submission.status = Submission.Status.DONE
        submission.verdict = result.verdict
        submission.score = result.score
        submission.subtask_scores = [s.model_dump(mode="json") for s in result.subtasks]
        submission.max_time_ms = round(result.max_time * 1000)
        submission.max_memory_kb = result.max_memory_kb
        submission.compile_log = _cap_utf8(result.compile_log, MAX_COMPILE_LOG_BYTES)
        submission.judged_at = timezone.now()
        submission.save()
        TestResult.objects.filter(submission=submission).delete()
        TestResult.objects.bulk_create([_test_row(submission, test) for test in result.tests])
        _update_best(submission, result.score)
        job.status = JudgeJob.Status.DONE
        job.lease_until = None
        job.save()
    return _no_content()


@_endpoint(api.Fail)
def fail(message: api.Fail) -> HttpResponse:
    """The worker couldn't judge the job: try again later, or give up after MAX_ATTEMPTS."""
    with transaction.atomic():
        job = _held_job(message.worker_id, message.job_id)
        if job is None:
            return _not_yours()
        job.error = message.error
        if job.attempts >= MAX_ATTEMPTS:
            _give_up(job, message.error)
        else:
            _requeue(job)
    return _no_content()
