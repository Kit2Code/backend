"""Opening (publishing) and closing (hiding) a problem: one rule for every admin page.

A problem may be opened only when its current version has passed validation, so students never
see a problem whose tests or reference solutions are broken.
"""

from django.db import transaction
from django.http import HttpRequest

from core import audit
from problems.models import Problem, ProblemVersion


def can_open(problem: Problem) -> bool:
    """True if `problem` has a current version and that version passed validation."""
    version = problem.current_version
    return version is not None and version.status == ProblemVersion.Status.VALID


def open_problem(request: HttpRequest, problem: Problem) -> bool:
    """Make `problem` public (audit-logged). False, and nothing changes, if it can't be opened."""
    with transaction.atomic():
        # Lock the problem row only (a version row is never changed here).
        locked = (
            Problem.objects.select_for_update(of=("self",))
            .select_related("current_version")
            .get(pk=problem.pk)
        )
        version = locked.current_version
        if version is None or not can_open(locked):
            return False
        if not locked.is_visible:
            locked.is_visible = True
            locked.save(update_fields=["is_visible", "updated_at"])
            audit.record(request, "problem.publish", locked, {"version": version.dir_name})
    problem.refresh_from_db()
    return True


def close_problem(request: HttpRequest, problem: Problem) -> None:
    """Hide `problem` from the public site (audit-logged)."""
    with transaction.atomic():
        changed = Problem.objects.filter(pk=problem.pk, is_visible=True).update(is_visible=False)
        if changed:
            audit.record(request, "problem.hide", problem)
    problem.refresh_from_db()
