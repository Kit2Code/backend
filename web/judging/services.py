"""Creating a submission, shared by the old submit page and the API (PLAN.md §5.3)."""

import math
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from core import ratelimit
from judging.models import JudgeJob, Submission
from problems.models import Problem, ProblemVersion


@dataclass(frozen=True)
class Refused:
    """Why the user may not submit now: `cooldown`, `too_many_waiting` or `hourly_limit`, and
    the seconds to wait when known (contract §9, `Retry-After`)."""

    code: str
    retry_after: int | None


def _hourly_limit() -> ratelimit.Limit:
    return ratelimit.Limit("submit-hour", settings.GRADER_SUBMIT_PER_HOUR, timedelta(hours=1))


def _refused(user: User) -> Refused | None:
    """Checked in this order inside the transaction that holds the user's row lock, so two submits
    sent at the same moment are checked one after the other. The hourly count is from
    core.ratelimit (see docs/DECISIONS.md)."""
    now = timezone.now()
    mine = Submission.objects.filter(user=user)
    cooldown = timedelta(seconds=settings.GRADER_SUBMIT_COOLDOWN_SECONDS)
    last = mine.filter(created_at__gt=now - cooldown).order_by("-created_at").first()
    if last is not None:
        return Refused("cooldown", math.ceil((last.created_at + cooldown - now).total_seconds()))
    waiting = mine.filter(status__in=[Submission.Status.QUEUED, Submission.Status.JUDGING])
    if waiting.count() >= settings.GRADER_SUBMIT_MAX_WAITING:
        return Refused("too_many_waiting", None)
    limit = _hourly_limit()
    if not ratelimit.hit(limit, str(user.pk), now):
        window_end = ratelimit.window_start(limit, now) + limit.window
        return Refused("hourly_limit", math.ceil((window_end - now).total_seconds()))
    return None


def create_submission(
    user_pk: int, problem: Problem, version: ProblemVersion, language: str, code: str
) -> Submission | Refused:
    """Queue `code` for judging, or say why not. `language` and `code` are already validated."""
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_pk)
        refused = _refused(user)
        if refused is not None:
            return refused
        submission = Submission.objects.create(
            user=user, problem=problem, problem_version=version, language=language, source=code
        )
        JudgeJob.objects.create(
            kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH, submission=submission
        )
    return submission
