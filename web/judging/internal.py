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
from django.db.models import F, Value
from django.db.models.functions import Greatest
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
from problems.models import ProblemVersion

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
    """The job won't be tried again: its submission becomes a judge error, or the problem
    version it was validating becomes invalid."""
    job.status = JudgeJob.Status.FAILED
    job.error = error
    job.lease_until = None
    job.save()
    if job.submission_id is not None:
        Submission.objects.filter(pk=job.submission_id).update(
            status=Submission.Status.DONE, verdict=Verdict.JUDGE_ERROR, judged_at=timezone.now()
        )
    if job.kind == JudgeJob.Kind.VALIDATE and job.problem_version_id is not None:
        ProblemVersion.objects.filter(pk=job.problem_version_id).update(
            status=ProblemVersion.Status.INVALID
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
    with transaction.atomic():
        _release_expired_leases()
        queued = JudgeJob.objects.filter(status=JudgeJob.Status.QUEUED)
        if grading_paused():
            # Paused: submissions wait, but the self-test still runs (it checks the judge itself).
            queued = queued.filter(kind=JudgeJob.Kind.SELFTEST)
        # skip_locked: two workers claiming at the same moment each skip the row the other has
        # locked, so they never get the same job and never wait for each other.
        job = queued.select_for_update(skip_locked=True).order_by("-priority", "created_at").first()
        if job is None or not _has_work(job):
            if job is not None:
                _give_up(job, "the job names no submission or problem version")
            _seen(message, taken=0)
            return _no_content()
        _seen(message, taken=1)
        job.status = JudgeJob.Status.RUNNING
        job.worker_id = message.worker_id
        job.attempts += 1
        job.lease_until = timezone.now() + LEASE
        job.save()
        # Built inside the transaction: if the job can't be described (say, a bad time_scale
        # setting), the error rolls the claim back, so the job stays queued and keeps its tries.
        payload = _describe(job)
    return JsonResponse(payload.model_dump(mode="json", exclude_none=True))


def _seen(message: api.ClaimRequest, taken: int) -> None:
    """The worker is alive; its slots in use are the ones it didn't offer, plus this claim's job."""
    Worker.objects.filter(worker_id=message.worker_id).update(
        last_seen_at=timezone.now(),
        busy_slots=Greatest(F("slots") - message.free_slots + taken, Value(0)),
    )


def _has_work(job: JudgeJob) -> bool:
    if job.kind == JudgeJob.Kind.JUDGE:
        return job.submission_id is not None
    if job.kind == JudgeJob.Kind.SELFTEST:
        return True
    return job.problem_version_id is not None


def _describe(job: JudgeJob) -> api.JudgeJob:
    """The job as the worker gets it. A submission is marked as being judged."""
    time_scale = SiteSetting.get().time_scale
    if job.kind == JudgeJob.Kind.JUDGE:
        submission = Submission.objects.select_related("problem", "problem_version").get(
            judgejob=job
        )
        submission.status = Submission.Status.JUDGING
        submission.save(update_fields=["status"])
        return api.JudgeJob(
            job_id=job.pk,
            kind="judge",
            problem_path=_problem_path(submission.problem_version),
            language=submission.language,
            source=submission.source,
            time_scale=time_scale,
        )
    if job.kind == JudgeJob.Kind.SELFTEST:
        return api.JudgeJob(job_id=job.pk, kind="selftest", time_scale=time_scale)
    version = ProblemVersion.objects.select_related("problem").get(judgejob=job)
    return api.JudgeJob(
        job_id=job.pk, kind=job.kind, problem_path=_problem_path(version), time_scale=time_scale
    )


def _problem_path(version: ProblemVersion) -> str:
    return f"{version.problem.slug}/{version.dir_name}"


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


def refresh_best(user_id: int, problem_id: int) -> None:
    """Work out the user's best score on the problem again from all their judged submissions.

    Used after a rejudge, which can lower a score; `_update_best` only ever raises it.
    """
    lookup = {"user_id": user_id, "problem_id": problem_id}
    best_submission = (
        Submission.objects.filter(**lookup, status=Submission.Status.DONE, score__isnull=False)
        .order_by("-score", "created_at")
        .first()
    )
    if best_submission is None or best_submission.score is None:
        UserProblemBest.objects.filter(**lookup).delete()
        return
    UserProblemBest.objects.update_or_create(
        user_id=user_id,
        problem_id=problem_id,
        defaults={
            "best_score": best_submission.score,
            "reached_at": best_submission.created_at,
            "submission": best_submission,
        },
    )


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
        if job.priority == JudgeJob.Priority.REJUDGE:
            refresh_best(submission.user_id, submission.problem_id)
        else:
            _update_best(submission, result.score)
        job.status = JudgeJob.Status.DONE
        job.lease_until = None
        job.save()
    return _no_content()


@_endpoint(api.Report)
def report(message: api.Report) -> HttpResponse:
    """The result of a job that isn't a submission: a validation, calibration or self-test."""
    with transaction.atomic():
        job = _held_job(message.worker_id, message.job_id)
        if job is None or job.kind != message.report.kind:
            return _not_yours()
        job.result = message.report.model_dump(mode="json")
        job.status = JudgeJob.Status.DONE
        job.lease_until = None
        job.save()
        if isinstance(message.report, api.ValidationReport):
            valid = message.report.ok
            ProblemVersion.objects.filter(judgejob=job).update(
                status=ProblemVersion.Status.VALID if valid else ProblemVersion.Status.INVALID
            )
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
