"""`{% icon "name" %}`: one of the vendored Lucide icons, inlined so it takes the text colour."""

import re
from functools import lru_cache
from pathlib import Path

from django import template
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

ICON_DIR = Path(__file__).resolve().parents[1] / "static" / "vendor" / "lucide"
ICON_NAME = re.compile(r"[a-z0-9-]+")


@lru_cache(maxsize=64)
def load_icon(name: str) -> str:
    """The icon's SVG without its licence comment, hidden from screen readers.

    Raises `TemplateSyntaxError` for a name that is not one of the vendored files.
    """
    path = ICON_DIR / f"{name}.svg"
    if not ICON_NAME.fullmatch(name) or not path.is_file():
        raise template.TemplateSyntaxError(f"unknown icon {name!r}")
    svg = path.read_text(encoding="utf-8")
    svg = svg[svg.index("<svg") :]  # drop the licence comment (the licence is in LICENSE)
    svg = re.sub(
        r'class="[^"]*"', 'class="icon" aria-hidden="true" focusable="false"', svg, count=1
    )
    return " ".join(svg.split())  # one line, so it sits inside links and buttons cleanly


@register.simple_tag
def icon(name: str) -> SafeString:
    """Inline the vendored icon `name` (our own files, so safe to mark as HTML)."""
    return mark_safe(load_icon(name))  # noqa: S308 - fixed files shipped with the code
