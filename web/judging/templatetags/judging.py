"""Template helpers for submissions."""

from django import template

from grader_common.problem_format import LANGUAGE_NAMES

register = template.Library()


@register.filter
def ms(milliseconds: int | None) -> str:
    """A time rounded to 10 ms ("1230 ms"): finer figures are mostly noise."""
    if milliseconds is None:
        return ""
    return f"{round(milliseconds, -1)} ms"


@register.filter
def mb(kilobytes: int | None) -> str:
    """Memory in MB with one decimal ("2.4 MB")."""
    if kilobytes is None:
        return ""
    return f"{kilobytes / 1024:.1f} MB"


@register.filter
def language_name(code: str) -> str:
    """The display name of a language code ("cpp17" → "C++17")."""
    return LANGUAGE_NAMES.get(code, code)
