"""Problem statements: Markdown rendered to safe HTML, or links to PDFs."""

from dataclasses import dataclass

import nh3
from markdown_it import MarkdownIt

from problems import store
from problems.models import ProblemVersion

MAX_STATEMENT_BYTES = 512 * 1024
LANGUAGES = ("th", "en")

# CommonMark with tables; raw HTML in the Markdown is shown as text, not run ("html": False).
_markdown = MarkdownIt("commonmark", {"html": False}).enable("table")

# What may stay after rendering. nh3 removes everything else (scripts, event handlers, styles).
_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "em", "strong", "code", "pre",
    "blockquote", "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
    "sub", "sup",
}  # fmt: skip
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt", "title"}, "ol": {"start"}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def render_markdown(text: str) -> str:
    """Markdown → HTML that is safe to put in a page (the only input we ever mark |safe)."""
    html = _markdown.render(text)
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )


@dataclass(frozen=True)
class Statement:
    html: str  # rendered Markdown, or "" when there is only a PDF
    language: str
    pdfs: dict[str, str]  # language -> file name inside the version, e.g. "statement/th.pdf"


def statement(version: ProblemVersion, language: str) -> Statement:
    """The statement in `language`, or in the other language if that is all there is."""
    order = [language, *[lang for lang in LANGUAGES if lang != language]]
    pdfs = {
        lang: f"statement/{lang}.pdf"
        for lang in order
        if store.exists(version, f"statement/{lang}.pdf")
    }
    for lang in order:
        name = f"statement/{lang}.md"
        if store.exists(version, name):
            text, _cut = store.read_text(version, name, MAX_STATEMENT_BYTES)
            return Statement(html=render_markdown(text), language=lang, pdfs=pdfs)
    return Statement(html="", language=next(iter(pdfs), language), pdfs=pdfs)
