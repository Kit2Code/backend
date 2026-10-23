"""The admin dashboard's numbers: queue, workers, grading state, problems, today's submissions."""

from dataclasses import dataclass

from django.db.models import Count, Q
from django.utils import timezone

from judging.admin_views import ONLINE_WITHIN, JudgeError, recent_judge_errors
from judging.internal import grading_paused
from judging.models import JudgeJob, Submission, Worker
from problems.models import Problem, ProblemVersion
from problems.tasks import start_of_today

RECENT_SUBMISSIONS = 10
RECENT_JUDGE_ERRORS = 5


@dataclass(frozen=True)
class Dashboard:
    queue: int
    workers_online: int
    paused: bool
    public: int
    hidden: int
    validating: int
    invalid: int
    submissions_today: int
    recent: list[Submission]
    judge_errors: list[JudgeError]


def dashboard() -> Dashboard:
    """Everything the dashboard shows, in a handful of queries."""
    now = timezone.now()
    problems = Problem.objects.aggregate(
        public=Count("pk", filter=Q(is_visible=True)),
        hidden=Count("pk", filter=Q(is_visible=False)),
        validating=Count("pk", filter=Q(current_version__status=ProblemVersion.Status.VALIDATING)),
        invalid=Count("pk", filter=Q(current_version__status=ProblemVersion.Status.INVALID)),
    )
    recent = list(
        Submission.objects.select_related("user", "problem").order_by("-created_at", "-pk")[
            :RECENT_SUBMISSIONS
        ]
    )
    return Dashboard(
        queue=JudgeJob.objects.filter(status=JudgeJob.Status.QUEUED).count(),
        workers_online=Worker.objects.filter(last_seen_at__gte=now - ONLINE_WITHIN).count(),
        paused=grading_paused(),
        public=problems["public"],
        hidden=problems["hidden"],
        validating=problems["validating"],
        invalid=problems["invalid"],
        submissions_today=Submission.objects.filter(created_at__gte=start_of_today()).count(),
        recent=recent,
        judge_errors=recent_judge_errors(RECENT_JUDGE_ERRORS),
    )
