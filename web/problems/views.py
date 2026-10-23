"""Public problem pages: the list and one problem."""

import os
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe
from django.views.decorators.vary import vary_on_headers

from accounts.models import User
from grader_common.problem_format import LANGUAGE_NAMES
from problems import archive as archive_counts
from problems import files, store, tasks
from problems.models import Problem, ProblemGroup, ProblemVersion
from problems.statements import statement

MAX_SAMPLE_BYTES = 16 * 1024
TASKS_PER_PAGE = 50
MAX_SEARCH_LENGTH = 100


def visible_problems() -> QuerySet[Problem]:
    """Problems the public may see: published, with a version in the store."""
    return Problem.objects.filter(is_visible=True, current_version__isnull=False)


def recent_problems(count: int = 5) -> list[Problem]:
    return list(visible_problems().order_by("-created_at")[:count])


def _signed_in_user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _task_table(
    request: HttpRequest, problems: QuerySet[Problem], *, search: str = "", group: int | None = None
) -> dict[str, object]:
    """The context for `problems/_task_results.html`: one page of rows, sorted as asked."""
    user = _signed_in_user(request)
    status = request.GET.get("status", "") if user is not None else ""
    sort = request.GET.get("sort", "id")
    if sort.removeprefix("-") not in tasks.SORT_COLUMNS:
        sort = "id"
    rows = tasks.rows(
        problems,
        user,
        search=search,
        group=group,
        status=status,
        sort=sort,
        language=get_language() or "th",
    )
    return {
        "page": Paginator(rows, TASKS_PER_PAGE).get_page(request.GET.get("page")),
        "status": status,
        "next_sorts": tasks.next_sorts(sort),
        "signed_in": user is not None,
    }


def _group_choices(groups: list[ProblemGroup], language: str) -> list[tuple[int, str]]:
    """(id, label) for the group filter: each top-level group followed by its subgroups."""
    choices = []
    for top in (g for g in groups if g.parent_id is None):
        choices.append((top.pk, top.name_in(language)))
        for sub in (g for g in groups if g.parent_id == top.pk):
            choices.append((sub.pk, f"{top.name_in(language)} / {sub.name_in(language)}"))
    return choices


@require_safe
@vary_on_headers("HX-Request")
def problem_list(request: HttpRequest) -> HttpResponse:
    """The Tasks page: stat cards, search, status and group filters, sortable columns, 50 per page.

    htmx searches (the `HX-Request` header) get only the results part of the page.
    """
    search = request.GET.get("q", "").strip()[:MAX_SEARCH_LENGTH]
    group_text = request.GET.get("group", "")
    group = int(group_text) if group_text.isascii() and group_text.isdigit() else None
    problems = visible_problems()
    context = {**_task_table(request, problems, search=search, group=group), "search": search}
    if request.headers.get("HX-Request"):
        return render(request, "problems/_task_results.html", context)
    language = get_language() or "th"
    context["counts"] = tasks.counts(problems, _signed_in_user(request))
    context["group"] = group
    context["group_choices"] = _group_choices(list(ProblemGroup.objects.all()), language)
    return render(request, "problems/list.html", context)


@require_safe
def archive(request: HttpRequest) -> HttpResponse:
    """The Archive: a card per top-level group, plus "other" for problems with no group."""
    user = _signed_in_user(request)
    groups = list(ProblemGroup.objects.all())
    counts = archive_counts.count_by_group(visible_problems(), user)
    cards = archive_counts.folders(
        [g for g in groups if g.parent_id is None],
        groups,
        counts,
        url_prefix="/archive",
        language=get_language() or "th",
        signed_in=user is not None,
    )
    ungrouped_total, ungrouped_solved = counts.get(None, (0, 0))
    if ungrouped_total:
        cards.append(
            archive_counts.Folder(
                url=f"/archive/{ProblemGroup.UNGROUPED_SLUG}",
                name=_("Other"),
                total=ungrouped_total,
                solved=ungrouped_solved if user is not None else None,
            )
        )
    return render(request, "problems/archive.html", {"folders": cards, "crumbs": []})


@require_safe
def archive_group(request: HttpRequest, group_slug: str, sub_slug: str = "") -> HttpResponse:
    """One folder of the Archive: its subgroups as cards, then its own problems as a table.

    404 for a group that does not exist at this place, or that has no visible problems.
    """
    language = get_language() or "th"
    problems = visible_problems()
    crumbs: list[tuple[str, str]] = [("/archive", _("Archive"))]
    if group_slug == ProblemGroup.UNGROUPED_SLUG and not sub_slug:
        own = problems.filter(group__isnull=True)
        if not own.exists():
            raise Http404
        other: dict[str, object] = {"title": _("Other"), "crumbs": crumbs, "folders": []}
        other.update(_task_table(request, own))
        return render(request, "problems/archive.html", other)
    top = get_object_or_404(ProblemGroup, slug=group_slug, parent__isnull=True)
    group = get_object_or_404(ProblemGroup, slug=sub_slug, parent=top) if sub_slug else top
    user = _signed_in_user(request)
    groups = list(ProblemGroup.objects.filter(Q(pk=group.pk) | Q(parent=group)))
    counts = archive_counts.count_by_group(problems.filter(group__in=groups), user)
    if not archive_counts.numbers(group, groups, counts)[0]:
        raise Http404
    if sub_slug:
        crumbs.append((f"/archive/{top.slug}", top.name_in(language)))
    context: dict[str, object] = {
        "title": group.name_in(language),
        "crumbs": crumbs,
        "folders": archive_counts.folders(
            [g for g in groups if g.parent_id == group.pk],
            groups,
            counts,
            url_prefix=f"/archive/{top.slug}",
            language=language,
            signed_in=user is not None,
        ),
    }
    if counts.get(group.pk, (0, 0))[0]:  # problems directly in this group
        context.update(_task_table(request, problems.filter(group=group)))
    return render(request, "problems/archive.html", context)


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
