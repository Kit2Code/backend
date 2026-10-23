"""The Archive's folder cards: problem groups with how many problems they hold and the user solved.

Two queries whatever the number of groups: all groups, and the visible problems counted per group.
A top-level folder's numbers include its subgroups' problems.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from django.db.models import Count, QuerySet

from accounts.models import User
from problems import tasks
from problems.models import Problem, ProblemGroup


@dataclass(frozen=True)
class Folder:
    """One card: where it leads, its name, and its counts (`solved` is None when signed out)."""

    url: str
    name: str
    total: int
    solved: int | None

    @property
    def percent(self) -> int:
        """Solved as a whole percentage of total (0 when signed out or empty)."""
        if not self.solved or not self.total:
            return 0
        return round(100 * self.solved / self.total)


# (total, solved) per group id; None is "no group".
Counts = dict[int | None, tuple[int, int]]


def count_by_group(problems: QuerySet[Problem], user: User | None) -> Counts:
    """How many of `problems` each group holds directly, and how many of them `user` solved."""
    if user is None:
        per_group = problems.values("group_id").annotate(total=Count("pk")).order_by()
        return {row["group_id"]: (row["total"], 0) for row in per_group}
    per_group = (
        tasks.with_mine(problems, user)
        .values("group_id")
        .annotate(total=Count("pk"), solved=Count("pk", filter=tasks.solved("mine")))
        .order_by()
    )
    return {row["group_id"]: (row["total"], row["solved"]) for row in per_group}


def numbers(
    group: ProblemGroup, all_groups: Iterable[ProblemGroup], counts: Counts
) -> tuple[int, int]:
    """(total, solved) for `group` together with its subgroups."""
    ids = {group.pk} | {g.pk for g in all_groups if g.parent_id == group.pk}
    total = sum(counts.get(pk, (0, 0))[0] for pk in ids)
    solved = sum(counts.get(pk, (0, 0))[1] for pk in ids)
    return total, solved


def folders(
    shown: Iterable[ProblemGroup],
    all_groups: list[ProblemGroup],
    counts: Counts,
    *,
    url_prefix: str,
    language: str,
    signed_in: bool,
) -> list[Folder]:
    """Cards for the `shown` groups, in order. Groups with no visible problems are left out."""
    cards = []
    for group in shown:
        total, solved = numbers(group, all_groups, counts)
        if total:
            cards.append(
                Folder(
                    url=f"{url_prefix}/{group.slug}",
                    name=group.name_in(language),
                    total=total,
                    solved=solved if signed_in else None,
                )
            )
    return cards
