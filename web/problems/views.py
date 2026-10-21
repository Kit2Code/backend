"""Public problem pages: the list and one problem."""

import os
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.db.models import QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import get_language
from django.views.decorators.http import require_safe

from grader_common.problem_format import LANGUAGE_NAMES
from problems import files, store
from problems.models import Problem, ProblemVersion
from problems.statements import statement

MAX_SAMPLE_BYTES = 16 * 1024


def visible_problems() -> QuerySet[Problem]:
    """Problems the public may see: published, with a version in the store."""
    return Problem.objects.filter(is_visible=True, current_version__isnull=False)


def recent_problems(count: int = 5) -> list[Problem]:
    return list(visible_problems().order_by("-created_at")[:count])


def problem_list(request: HttpRequest) -> HttpResponse:
    problems = visible_problems().prefetch_related("tags").order_by("slug")
    return render(request, "problems/list.html", {"problems": problems})


@dataclass(frozen=True)
class Sample:
    name: str
    input: str
    output: str
    cut_short: bool


def _samples(version: ProblemVersion) -> list[Sample]:
    samples = []
    for name in store.problem_format(version).samples:
        given, cut_in = store.read_text(version, f"tests/{name}.in", MAX_SAMPLE_BYTES)
        wanted, cut_out = store.read_text(version, f"tests/{name}.out", MAX_SAMPLE_BYTES)
        samples.append(Sample(name, given, wanted, cut_in or cut_out))
    return samples


def problem_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """One problem: statement, limits, languages and samples. Hidden problems don't exist here."""
    problem = get_object_or_404(visible_problems().select_related("current_version"), slug=slug)
    version = problem.current_version
    if version is None:  # excluded by visible_problems(); this check is for the type checker
        raise Http404
    language = get_language() or "th"
    fmt = store.problem_format(version)
    context = {
        "problem": problem,
        "title": problem.title_in(language),
        "format": fmt,
        "languages": [LANGUAGE_NAMES[code] for code in fmt.languages],
        "statement": statement(version, language),
        "samples": _samples(version),
        "tags": problem.tags.all(),
        "attachments": files.attachments(version),
    }
    return render(request, "problems/detail.html", context)


@require_safe
def problem_file(request: HttpRequest, slug: str, name: str) -> FileResponse:
    """Serve one file of a visible problem, if it is on the allow-list (problems.files)."""
    problem = get_object_or_404(visible_problems().select_related("current_version"), slug=slug)
    version = problem.current_version
    if version is None or name not in files.downloadable(version):
        raise Http404
    try:
        fd = store.open_file(version, name)
    except store.NotInStore:
        raise Http404 from None
    return FileResponse(
        os.fdopen(fd, "rb"),
        content_type=files.content_type(name),
        as_attachment=not files.opens_in_browser(name),
        filename=PurePosixPath(name).name,
    )
