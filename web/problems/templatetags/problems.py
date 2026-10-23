"""Template helpers for problems."""

from django import template
from django.utils.translation import get_language

from problems import tasks
from problems.models import Problem

register = template.Library()


@register.simple_tag
def problem_title(problem: Problem) -> str:
    """The problem's title in the current language."""
    return problem.title_in(get_language() or "th")


@register.simple_tag
def task_status(problem: Problem) -> str:
    """ "solved", "tried" or "untried" for a row of the Tasks page (signed-in users only)."""
    return tasks.status_of(problem)


@register.filter
def seconds(milliseconds: int) -> str:
    """A time limit in seconds without trailing zeros: 1000 -> "1", 1500 -> "1.5"."""
    return f"{milliseconds / 1000:g}"
