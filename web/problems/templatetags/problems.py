"""Template helpers for problems."""

from django import template
from django.utils.translation import get_language

from problems.models import Problem

register = template.Library()


@register.simple_tag
def problem_title(problem: Problem) -> str:
    """The problem's title in the current language."""
    return problem.title_in(get_language() or "th")
