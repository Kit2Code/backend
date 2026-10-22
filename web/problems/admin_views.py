"""Admin pages for problems: upload a zip, see its import report and validation, publish it.

Routed by our admin site (config.admin.GraderAdminSite.get_urls), which only lets staff in.
Each page also needs the "change problem" permission.
"""

import re
import shutil
import tempfile
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin import site
from django.contrib.auth.decorators import permission_required
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from core import audit
from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import ImportResult, import_problem
from judging.models import JudgeJob
from problems import registry, store
from problems.models import Problem, ProblemVersion

CHANGE_PROBLEM = "problems.change_problem"


class UploadForm(forms.Form):
    file = forms.FileField(label=gettext_lazy("Problem zip (native or CMS Italian format)"))

    def clean_file(self) -> "UploadedFile[bytes]":
        upload: UploadedFile[bytes] = self.cleaned_data["file"]
        if (upload.size or 0) > settings.GRADER_MAX_UPLOAD_BYTES:
            limit_mb = settings.GRADER_MAX_UPLOAD_BYTES // (1024 * 1024)
            raise forms.ValidationError(_("The file is larger than %(mb)d MB.") % {"mb": limit_mb})
        return upload


@permission_required(CHANGE_PROBLEM, raise_exception=True)
def import_page(request: HttpRequest) -> HttpResponse:
    """Upload a problem zip. It is imported hidden and queued for validation."""
    form = UploadForm(request.POST or None, request.FILES or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        try:
            result = _import_upload(form.cleaned_data["file"])
        except ProblemImportError as problem:
            error = str(problem)
        else:
            version = _register(request, result)
            return redirect("admin:grader-version", version.pk)
    context = {**site.each_context(request), "form": form, "error": error,
               "title": _("Import a problem")}  # fmt: skip
    return render(request, "admin/grader/import.html", context)


def _import_upload(upload: "UploadedFile[bytes]") -> ImportResult:
    """Save the upload in a private temporary folder under the uploads, import it, clean up."""
    uploads = Path(settings.GRADER_UPLOADS_DIR) / "tmp"
    uploads.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(dir=uploads))
    try:
        # Only a safe version of the name is kept: it ends up in the import report.
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(upload.name or "").name)[:100] or "upload.zip"
        target = folder / name
        with target.open("wb") as file:
            for chunk in upload.chunks():
                file.write(chunk)
        return import_problem(target, Path(settings.GRADER_PROBLEMS_DIR))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _register(request: HttpRequest, result: ImportResult) -> ProblemVersion:
    with transaction.atomic():
        version = registry.register(result, replace_published=False)
        if version.status != ProblemVersion.Status.VALID:
            _queue_validation(version)
    details = {"version": version.dir_name, "new": result.created, "warnings": len(result.warnings)}
    audit.record(request, "problem.import", version.problem, details)
    return version


def _queue_validation(version: ProblemVersion) -> None:
    """Queue a validation job for `version`, unless one is already waiting or running."""
    waiting = JudgeJob.objects.filter(
        kind=JudgeJob.Kind.VALIDATE,
        problem_version=version,
        status__in=[JudgeJob.Status.QUEUED, JudgeJob.Status.RUNNING],
    )
    if not waiting.exists():
        JudgeJob.objects.create(
            kind=JudgeJob.Kind.VALIDATE,
            priority=JudgeJob.Priority.VALIDATION,
            problem_version=version,
        )
    version.status = ProblemVersion.Status.VALIDATING
    version.save(update_fields=["status"])


@permission_required(CHANGE_PROBLEM, raise_exception=True)
def version_page(request: HttpRequest, pk: int) -> HttpResponse:
    """One imported version: its import report, the validation result, and Publish."""
    version = get_object_or_404(ProblemVersion.objects.select_related("problem"), pk=pk)
    validation = (
        JudgeJob.objects.filter(kind=JudgeJob.Kind.VALIDATE, problem_version=version)
        .order_by("-created_at")
        .first()
    )
    problem = version.problem
    context = {
        **site.each_context(request),
        "title": f"{problem.slug} / {version.dir_name}",
        "version": version,
        "problem": problem,
        "is_current": problem.current_version_id == version.pk,
        "validation": validation,
        "validating": version.status == ProblemVersion.Status.VALIDATING,
        "can_publish": version.status == ProblemVersion.Status.VALID
        and not (problem.is_visible and problem.current_version_id == version.pk),
    }
    return render(request, "admin/grader/version.html", context)


@require_POST
@permission_required(CHANGE_PROBLEM, raise_exception=True)
def publish_version(request: HttpRequest, pk: int) -> HttpResponse:
    """Make a validated version the problem's current one, and the problem public."""
    with transaction.atomic():
        version = get_object_or_404(ProblemVersion.objects.select_for_update(), pk=pk)
        if version.status != ProblemVersion.Status.VALID:
            messages.error(request, _("Only a version that passed validation can be published."))
            return redirect("admin:grader-version", pk)
        problem = Problem.objects.select_for_update().get(pk=version.problem_id)
        registry.make_current(problem, version, store.problem_format(version))
        problem.is_visible = True
        problem.save(update_fields=["is_visible"])
    audit.record(request, "problem.publish", problem, {"version": version.dir_name})
    messages.success(request, _("Published."))
    return redirect("admin:grader-version", pk)


@require_POST
@permission_required(CHANGE_PROBLEM, raise_exception=True)
def validate_version(request: HttpRequest, pk: int) -> HttpResponse:
    """Run the reference solutions of this version again."""
    with transaction.atomic():
        version = get_object_or_404(ProblemVersion.objects.select_for_update(), pk=pk)
        _queue_validation(version)
    audit.record(request, "problem.validate", version.problem, {"version": version.dir_name})
    return redirect("admin:grader-version", pk)
