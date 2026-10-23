"""The numbers on a user's profile: their title, rank, score, and solved and tried problems."""

from dataclasses import dataclass
from typing import Any

from django.db.models import Max, Q, QuerySet, Sum
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from accounts.models import User
from judging.models import UserProblemBest
from problems.tasks import FULL_MARKS_SLACK
from problems.views import visible_problems


@dataclass(frozen=True)
class Title:
    """A title earned by solving problems (Codeforces-style names and colours, without ratings)."""

    key: str
    min_solved: int
    name: str | Promise


TITLES = [
    Title("newbie", 0, _("Newbie")),
    Title("pupil", 10, _("Pupil")),
    Title("specialist", 30, _("Specialist")),
    Title("expert", 75, _("Expert")),
    Title("master", 150, _("Master")),
]


def title_for(solved: int) -> Title:
    """The highest title whose `min_solved` the user has reached."""
    return [title for title in TITLES if solved >= title.min_solved][-1]


def ranking_rows() -> QuerySet[Any, Any]:
    """One row per ranked user, best first: total of best scores on visible problems.

    Ties go to whoever reached their total first, then to the username. Disabled users and zero
    totals are left out.
    """
    return (
        UserProblemBest.objects.filter(problem__in=visible_problems(), user__is_active=True)
        .values(
            "user__username",
            "user__display_name",
            "user__photo_updated_at",
            "user__show_photo",
        )
        .annotate(total=Sum("best_score"), finished_at=Max("reached_at"))
        .filter(total__gt=0)
        .order_by("-total", "finished_at", "user__username")
    )


@dataclass(frozen=True)
class Standing:
    total: float
    rank: int | None  # None: not in the ranking (no score yet)


def standing(person: User) -> Standing:
    """`person`'s total and their place in the ranking, in two queries."""
    mine = ranking_rows().filter(user=person).first()
    if mine is None:
        return Standing(total=0, rank=None)
    total, finished_at = mine["total"], mine["finished_at"]
    ahead = (
        ranking_rows()
        .filter(
            Q(total__gt=total)
            | Q(total=total, finished_at__lt=finished_at)
            | Q(total=total, finished_at=finished_at, user__username__lt=person.username)
        )
        .count()
    )
    return Standing(total=float(total), rank=ahead + 1)


def solved_and_tried(person: User) -> tuple[list[UserProblemBest], list[UserProblemBest]]:
    """`person`'s best-score rows on visible problems: those with full marks, and the rest."""
    bests = (
        UserProblemBest.objects.filter(user=person, problem__in=visible_problems())
        .select_related("problem")
        .order_by("problem__slug")
    )
    solved: list[UserProblemBest] = []
    tried: list[UserProblemBest] = []
    for row in bests:
        maximum = row.problem.max_score
        full_marks = maximum is not None and row.best_score >= maximum - FULL_MARKS_SLACK
        (solved if full_marks else tried).append(row)
    return solved, tried
