"""The Tasks page's data: the signed-in user's counts and the filtered, sorted problem rows.

Everything comes from the database (titles, limits and max score are copied from problem.yaml
when a version is made current), so the page reads no files and runs a fixed number of queries.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import (
    CharField,
    Count,
    F,
    FilteredRelation,
    Func,
    OrderBy,
    Q,
    QuerySet,
    Value,
)
from django.db.models.functions import Coalesce, Lower, NullIf
from django.utils import timezone

from accounts.models import User
from problems.models import Problem

# Scores are sums of floats: "full marks" allows for rounding, as the profile page does.
FULL_MARKS_SLACK = 1e-9

STATUSES = ("solved", "tried", "untried")
SORT_COLUMNS = ("id", "title", "passes")


@dataclass(frozen=True)
class Counts:
    """The stat cards. Only `all` is shown to someone who is signed out."""

    all: int
    solved: int = 0
    tried: int = 0
    untried: int = 0
    today: int = 0


def solved(prefix: str) -> Q:
    """Full marks on the best-score row reached through `prefix`."""
    return Q(**{f"{prefix}__best_score__gte": F("max_score") - FULL_MARKS_SLACK})


def with_mine(problems: QuerySet[Problem], user: User) -> QuerySet[Problem]:
    """Join each problem to at most one row: this user's best score on it."""
    return problems.annotate(
        mine=FilteredRelation("userproblembest", condition=Q(userproblembest__user=user))
    )


def start_of_today() -> datetime:
    """Midnight at the start of today, in the site's time zone."""
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def counts(problems: QuerySet[Problem], user: User | None) -> Counts:
    """The stat cards for `problems` (the visible ones), in one query."""
    if user is None:
        return Counts(all=problems.count())
    totals = with_mine(problems, user).aggregate(
        all=Count("pk"),
        attempted=Count("mine"),
        solved=Count("pk", filter=solved("mine")),
        today=Count("pk", filter=solved("mine") & Q(mine__reached_at__gte=start_of_today())),
    )
    return Counts(
        all=totals["all"],
        solved=totals["solved"],
        tried=totals["attempted"] - totals["solved"],
        untried=totals["all"] - totals["attempted"],
        today=totals["today"],
    )


def rows(
    problems: QuerySet[Problem],
    user: User | None,
    *,
    search: str = "",
    group: int | None = None,
    status: str = "",
    sort: str = "id",
    language: str = "th",
) -> QuerySet[Problem]:
    """The problems to list, each with `passes` (active users with full marks) and, for a
    signed-in user, `my_best` (their best score, or None)."""
    if search:
        matches = (
            Q(slug__icontains=search)
            | Q(title_th__icontains=search)
            | Q(title_en__icontains=search)
        )
        if search.isascii() and search.isdigit():  # the number in the "#" column
            matches |= Q(pk=int(search))
        problems = problems.filter(matches)
    if group is not None:  # the group and its subgroups
        problems = problems.filter(Q(group_id=group) | Q(group__parent_id=group))
    passes = Count(
        "userproblembest",
        filter=solved("userproblembest") & Q(userproblembest__user__is_active=True),
        distinct=True,
    )
    problems = problems.annotate(passes=passes)
    if user is not None:
        problems = with_mine(problems, user).annotate(my_best=F("mine__best_score"))
        if status == "solved":
            problems = problems.filter(solved("mine"))
        elif status == "tried":
            problems = problems.filter(mine__isnull=False).exclude(solved("mine"))
        elif status == "untried":
            problems = problems.filter(mine__isnull=True)
    return problems.order_by(*_ordering(sort, language))


def _ordering(sort: str, language: str) -> list[OrderBy | str]:
    """ORDER BY for `sort` ("id", "title" or "passes", "-" first for descending)."""
    descending = sort.startswith("-")
    column = sort.removeprefix("-")
    if column == "title":
        first, second = ("title_en", "title_th") if language == "en" else ("title_th", "title_en")
        # The title as shown: this language's, else the other one's, else the slug.
        key: F | Func = Lower(
            Coalesce(
                NullIf(F(first), Value("")),
                NullIf(F(second), Value("")),
                F("slug"),
                output_field=CharField(),
            )
        )
    elif column == "passes":
        key = F("passes")
    else:
        key = F("pk")
    return [key.desc() if descending else key.asc(), "pk"]


def status_of(problem: Problem) -> str:
    """ "solved", "tried" or "untried" for a row from `rows()` with a signed-in user."""
    best = getattr(problem, "my_best", None)
    if best is None:
        return "untried"
    if problem.max_score is not None and best >= problem.max_score - FULL_MARKS_SLACK:
        return "solved"
    return "tried"


def next_sorts(current: str) -> dict[str, str]:
    """For each column header, the sort its link asks for: the other direction if it is the
    current sort, else ascending (passes: descending first, most passed on top)."""
    links = {}
    for column in SORT_COLUMNS:
        if current == column:
            links[column] = "-" + column
        elif current == "-" + column:
            links[column] = column
        else:
            links[column] = "-passes" if column == "passes" else column
    return links
