"""Admin pages about the judge: System (workers, env report, queue, judge errors, self-test) and
Calibrate (time the reference solutions, suggest a time scale).

Routed by our admin site (config.admin.GraderAdminSite.get_urls), which only lets staff in.
System needs "view worker" (running the self-test: "add judge job"); Calibrate needs "change site
settings", like the time scale itself.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.contrib.admin import site
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from core import audit
from core.models import SiteSetting
from judging.calibration import suggested_time_scale
from judging.models import JudgeJob, Submission, Verdict, Worker
from problems.models import Problem

# A worker claims every 2 s while it has a free slot and says hello every 10 min; while all its
# slots are busy it only sends heartbeats, which don't count. 30 s without a claim is "offline".
ONLINE_WITHIN = timedelta(seconds=30)
# The worker id is its container's hostname, so every recreated container (an update, a dev
# restart) is a new worker; ones not seen for a week are left off the list.
LISTED_WITHIN = timedelta(days=7)
JUDGE_ERRORS_SHOWN = 50
WAITING = (JudgeJob.Status.QUEUED, JudgeJob.Status.RUNNING)


@dataclass(frozen=True)
class WorkerRow:
    worker: Worker
    online: bool
    report_json: str


@dataclass(frozen=True)
class JudgeError:
    at: datetime | None
    what: str
    error: str
    submission_id: int | None


@permission_required("judging.view_worker", raise_exception=True)
def system_page(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        return _run_selftest(request)
    now = timezone.now()
    workers = [
        WorkerRow(
            worker=worker,
            online=worker.last_seen_at is not None and now - worker.last_seen_at < ONLINE_WITHIN,
            report_json=json.dumps(worker.env_report, indent=2, ensure_ascii=False),
        )
        for worker in Worker.objects.filter(last_seen_at__gte=now - LISTED_WITHIN).order_by(
            "-last_seen_at"
        )
    ]
    queued = dict(
        JudgeJob.objects.filter(status=JudgeJob.Status.QUEUED)
        .values_list("kind")
        .annotate(n=Count("pk"))
        .values_list("kind", "n")
    )
    selftest = JudgeJob.objects.filter(kind=JudgeJob.Kind.SELFTEST).order_by("-created_at").first()
    context = {
        **site.each_context(request),
        "title": _("System"),
        "workers": workers,
        "queued": [(kind, label, queued.get(kind, 0)) for kind, label in JudgeJob.Kind.choices],
        "running": JudgeJob.objects.filter(status=JudgeJob.Status.RUNNING).count(),
        "judge_errors": recent_judge_errors(),
        "selftest": selftest,
        "selftest_ok": _selftest_ok(selftest),
        "selftest_waiting": selftest is not None and selftest.status in WAITING,
    }
    return render(request, "admin/grader/system.html", context)


def recent_judge_errors(limit: int = JUDGE_ERRORS_SHOWN) -> list[JudgeError]:
    """The newest judge errors: submissions judged JE, and jobs the worker gave up on."""
    errors = []
    failed_jobs = JudgeJob.objects.filter(status=JudgeJob.Status.FAILED).order_by("-updated_at")
    for job in failed_jobs[:limit]:
        what = f"{job.get_kind_display()} #{job.pk}"
        errors.append(JudgeError(job.updated_at, what, job.error, job.submission_id))
    # Submissions judged JE by the worker itself (no failed job), e.g. isolate broke on one test.
    listed = [error.submission_id for error in errors if error.submission_id is not None]
    judged_je = Submission.objects.filter(verdict=Verdict.JUDGE_ERROR).exclude(pk__in=listed)
    for submission in judged_je.order_by("-judged_at")[:limit]:
        what = f"submission #{submission.pk}"
        errors.append(JudgeError(submission.judged_at, what, "", submission.pk))
    long_ago = datetime(1970, 1, 1, tzinfo=UTC)
    errors.sort(key=lambda error: error.at or long_ago, reverse=True)
    return errors[:limit]


def _selftest_ok(job: JudgeJob | None) -> bool | None:
    """True or False for a finished self-test; None while there is none or it is still running."""
    if job is None or job.status != JudgeJob.Status.DONE:
        return None
    cases = job.result.get("cases", [])
    return bool(cases) and all(case.get("ok") for case in cases)


def _run_selftest(request: HttpRequest) -> HttpResponse:
    """Queue a self-test job (one at a time). It runs even while grading is paused."""
    if request.POST.get("action") != "selftest" or not request.user.has_perm(
        "judging.add_judgejob"
    ):
        raise PermissionDenied
    waiting = JudgeJob.objects.filter(kind=JudgeJob.Kind.SELFTEST, status__in=WAITING)
    if not waiting.exists():
        job = JudgeJob.objects.create(
            kind=JudgeJob.Kind.SELFTEST, priority=JudgeJob.Priority.BACKGROUND
        )
        audit.record(request, "system.selftest", job)
    return redirect("admin:grader-system")


@dataclass(frozen=True)
class CalibrationRow:
    problem: Problem
    job: JudgeJob | None
    suggested: float | None


@permission_required("core.change_sitesetting", raise_exception=True)
def calibrate_page(request: HttpRequest) -> HttpResponse:
    """Per problem: time its current version's reference solutions; suggest a time scale."""
    if request.method == "POST":
        return _calibrate(request)
    problems = Problem.objects.filter(current_version__isnull=False).select_related(
        "current_version"
    )
    rows = []
    for problem in problems.order_by("slug"):
        job = (
            JudgeJob.objects.filter(
                kind=JudgeJob.Kind.CALIBRATE, problem_version=problem.current_version
            )
            .order_by("-created_at")
            .first()
        )
        suggested = None
        if job is not None and job.status == JudgeJob.Status.DONE:
            suggested = suggested_time_scale(job.result)
        rows.append(CalibrationRow(problem, job, suggested))
    suggestions = [row.suggested for row in rows if row.suggested is not None]
    context = {
        **site.each_context(request),
        "title": _("Calibrate"),
        "rows": rows,
        "overall": max(suggestions) if suggestions else None,
        "time_scale": SiteSetting.get().time_scale,
        "waiting": any(row.job is not None and row.job.status in WAITING for row in rows),
    }
    return render(request, "admin/grader/calibrate.html", context)


def _calibrate(request: HttpRequest) -> HttpResponse:
    """Queue a calibration of one problem's current version (one at a time per version)."""
    problem = get_object_or_404(
        Problem.objects.filter(current_version__isnull=False), pk=request.POST.get("problem", "")
    )
    version = problem.current_version
    waiting = JudgeJob.objects.filter(
        kind=JudgeJob.Kind.CALIBRATE, problem_version=version, status__in=WAITING
    )
    if not waiting.exists():
        JudgeJob.objects.create(
            kind=JudgeJob.Kind.CALIBRATE,
            priority=JudgeJob.Priority.BACKGROUND,
            problem_version=version,
        )
        audit.record(request, "problem.calibrate", problem, {"version": str(version)})
    return redirect("admin:grader-calibrate")
