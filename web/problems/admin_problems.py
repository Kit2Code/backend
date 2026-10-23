"""The admin Problems page: every problem in one table, open/close in one click, groups.

Routed by our admin site (config.admin.GraderAdminSite.get_urls), which only lets staff in. Every
view also needs the "change problem" permission. The row forms are plain POST forms with a CSRF
token; with JavaScript, htmx sends them and swaps in the returned row (the `HX-Request` header),
without it they redirect back to the page.
"""

from pathlib import Path
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin import site
from django.contrib.auth.decorators import permission_required
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from core import audit
from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import import_problem
from grader_common.problem_format import LANGUAGE_NAMES
from problems import admin_views, editor, publishing
from problems.editor import EditorForm
from problems.models import Problem, ProblemGroup, ProblemVersion
from problems.statements import render_markdown

CHANGE_PROBLEM = "problems.change_problem"
STATES = ("open", "closed", "invalid")


class GroupForm(forms.ModelForm):  # type: ignore[type-arg]
    """A new Archive group. The model's clean() keeps groups two levels deep."""

    # Only top-level groups can hold subgroups.
    parent = forms.ModelChoiceField(
        queryset=ProblemGroup.objects.filter(parent__isnull=True),
        required=False,
        label=gettext_lazy("Inside group"),
    )

    class Meta:
        model = ProblemGroup
        fields = ["slug", "name_th", "name_en", "parent", "order"]
        labels = {
            "slug": gettext_lazy("Short name in the address"),
            "name_th": gettext_lazy("Name (Thai)"),
            "name_en": gettext_lazy("Name (English)"),
            "order": gettext_lazy("Order"),
        }


def _problems(request: HttpRequest) -> QuerySet[Problem]:
    """All problems, narrowed by the page's search box and filters."""
    problems = Problem.objects.select_related("current_version", "group__parent").order_by("slug")
    search = request.GET.get("q", "").strip()[:100]
    if search:
        problems = problems.filter(
            Q(slug__icontains=search)
            | Q(title_th__icontains=search)
            | Q(title_en__icontains=search)
        )
    group = request.GET.get("group", "")
    if group == "none":
        problems = problems.filter(group__isnull=True)
    elif group.isascii() and group.isdigit():
        problems = problems.filter(Q(group_id=int(group)) | Q(group__parent_id=int(group)))
    state = request.GET.get("state", "")
    if state == "open":
        problems = problems.filter(is_visible=True)
    elif state == "closed":
        problems = problems.filter(is_visible=False)
    elif state == "invalid":
        problems = problems.filter(current_version__status=ProblemVersion.Status.INVALID)
    return problems


def _groups() -> list[ProblemGroup]:
    """Every group, each top-level one followed by its subgroups."""
    groups = list(ProblemGroup.objects.select_related("parent"))
    ordered = []
    for top in (g for g in groups if g.parent_id is None):
        ordered.append(top)
        ordered.extend(g for g in groups if g.parent_id == top.pk)
    return ordered


def _row_context(
    problem: Problem, groups: list[ProblemGroup], error: str = ""
) -> dict[str, object]:
    return {
        "problem": problem,
        "groups": groups,
        "can_open": publishing.can_open(problem),
        "error": error,
    }


@permission_required(CHANGE_PROBLEM, raise_exception=True)
def problems_page(request: HttpRequest, group_form: GroupForm | None = None) -> HttpResponse:
    """The table of every problem, the filters, and the form for a new group."""
    groups = _groups()
    context = {
        **site.each_context(request),
        "title": _("Problems"),
        "rows": [_row_context(problem, groups) for problem in _problems(request)],
        "groups": groups,
        "states": STATES,
        "search": request.GET.get("q", ""),
        "group_filter": request.GET.get("group", ""),
        "state_filter": request.GET.get("state", ""),
        "group_form": group_form or GroupForm(),
    }
    return render(request, "admin/grader/problems.html", context)


def _row_or_back(
    request: HttpRequest, problem: Problem, error: str = "", status: int = 200
) -> HttpResponse:
    """For htmx, the updated row; otherwise back to the page (keeping its filters)."""
    if request.headers.get("HX-Request"):
        context = _row_context(problem, _groups(), error)
        return render(request, "admin/grader/_problem_row.html", context, status=status)
    if error:
        messages.error(request, f"{problem.slug}: {error}")
    back = reverse("admin:grader-problems")
    query = request.POST.get("back", "")
    return redirect(f"{back}?{query}" if query else back)


@require_POST
@permission_required(CHANGE_PROBLEM, raise_exception=True)
def problem_visibility(request: HttpRequest, pk: int) -> HttpResponse:
    """Open (publish) or close (hide) one problem. Opening needs a validated current version."""
    problem = get_object_or_404(Problem.objects.select_related("current_version", "group"), pk=pk)
    action = request.POST.get("action")
    if action == "open":
        if not publishing.open_problem(request, problem):
            error = _("It can't be opened: its current version has not passed validation.")
            return _row_or_back(request, problem, error)
    elif action == "close":
        publishing.close_problem(request, problem)
    else:
        return HttpResponseBadRequest()
    return _row_or_back(request, problem)


@require_POST
@permission_required(CHANGE_PROBLEM, raise_exception=True)
def problem_group(request: HttpRequest, pk: int) -> HttpResponse:
    """Put one problem in a group, or in none ("")."""
    problem = get_object_or_404(Problem.objects.select_related("current_version"), pk=pk)
    value = request.POST.get("group", "")
    group = None
    if value:
        if not (value.isascii() and value.isdigit()):
            return HttpResponseBadRequest()
        group = ProblemGroup.objects.filter(pk=int(value)).first()
        if group is None:
            return HttpResponseBadRequest()
    problem.group = group
    problem.save(update_fields=["group", "updated_at"])
    audit.record(request, "problem.group", problem, {"group": str(group) if group else ""})
    return _row_or_back(request, problem)


@require_POST
@permission_required("problems.add_problemgroup", raise_exception=True)
def add_group(request: HttpRequest) -> HttpResponse:
    """Create an Archive group from the form on the Problems page."""
    form = GroupForm(request.POST)
    if not form.is_valid():
        return problems_page(request, group_form=form)
    group = form.save()
    audit.record(request, "group.add", group)
    messages.success(request, _("Group added."))
    return redirect(reverse("admin:grader-problems") + "?" + urlencode({"group": group.pk}))


# ---- creating and editing a problem in the browser (problems/editor.py) ----


def _editor_page(
    request: HttpRequest, form: EditorForm, problem: Problem | None, error: str = ""
) -> HttpResponse:
    title = _("Edit %(slug)s") % {"slug": problem.slug} if problem else _("New problem")
    context = {
        **site.each_context(request),
        "title": title,
        "form": form,
        "problem": problem,
        "error": error,
        "solution_slots": [
            (form[f"solution{slot}"], form[f"solution{slot}_lang"], form[f"solution{slot}_score"])
            for slot in editor.SOLUTION_SLOTS
        ],
    }
    return render(request, "admin/grader/problem_form.html", context)


def _save_from_editor(
    request: HttpRequest, form: EditorForm, base: ProblemVersion | None
) -> ProblemVersion:
    """Build the folder, import it like a zip, register and queue validation, set the group.

    Raises ProblemImportError with a message for the user; nothing is stored then.
    """
    with editor.scratch_folder() as work:
        folder = work / "problem"
        editor.build_folder(form.cleaned_data, base, folder)
        result = import_problem(folder, Path(settings.GRADER_PROBLEMS_DIR))
    version = admin_views._register(request, result)
    Problem.objects.filter(pk=version.problem_id).update(group=form.cleaned_data.get("group"))
    return version


@permission_required(CHANGE_PROBLEM, raise_exception=True)
def problem_new(request: HttpRequest) -> HttpResponse:
    """A new problem from the browser: statement, tests, scoring, checker, solutions."""
    if request.method != "POST":
        initial = {"time_limit_ms": 1000, "memory_limit_mb": 256, "scoring": "sum",
                   "total": 100, "checker": "diff", "languages": list(LANGUAGE_NAMES)}  # fmt: skip
        return _editor_page(request, EditorForm(initial=initial), None)
    form = EditorForm(request.POST, request.FILES)
    if not form.is_valid():
        return _editor_page(request, form, None)
    existing = Problem.objects.filter(slug=form.cleaned_data["slug"]).first()
    if existing is not None:
        edit_url = reverse("admin:grader-problem-edit", args=[existing.pk])
        message = format_html(
            _('A problem with this short name exists already: <a href="{}">edit it</a>.'),
            edit_url,
        )
        return _editor_page(request, form, None, message)
    try:
        version = _save_from_editor(request, form, None)
    except ProblemImportError as problem:
        return _editor_page(request, form, None, str(problem))
    return redirect("admin:grader-version", version.pk)


@permission_required(CHANGE_PROBLEM, raise_exception=True)
def problem_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit a problem: starts from its current version; saving makes a new version."""
    problem = get_object_or_404(Problem.objects.select_related("current_version"), pk=pk)
    base = problem.current_version
    if base is None:
        messages.error(request, _("This problem has no version to start from: import it first."))
        return redirect("admin:grader-problems")
    initial = editor.initial_from(base)
    if request.method != "POST":
        return _editor_page(request, EditorForm(initial=initial, editing=True), problem)
    form = EditorForm(request.POST, request.FILES, initial=initial, editing=True)
    if not form.is_valid():
        return _editor_page(request, form, problem)
    try:
        version = _save_from_editor(request, form, base)
    except ProblemImportError as error:
        return _editor_page(request, form, problem, str(error))
    return redirect("admin:grader-version", version.pk)


@require_POST
@permission_required(CHANGE_PROBLEM, raise_exception=True)
def statement_preview(request: HttpRequest) -> HttpResponse:
    """The Markdown statement as the problem page will show it (rendered and sanitized)."""
    text = request.POST.get("statement_th") or request.POST.get("statement_en") or ""
    html = render_markdown(text[: editor.MAX_PREVIEW_CHARS])
    # Rendered by markdown-it and cleaned by nh3 (problems/statements.py): safe to show.
    return HttpResponse(format_html('<div class="statement-preview">{}</div>', mark_safe(html)))  # noqa: S308
