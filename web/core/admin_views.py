"""Admin pages for the site as a whole: Grading (pause, pause windows, time scale), Backups, and
Sign-in (the Google client).

Routed by our admin site (config.admin.GraderAdminSite.get_urls), which only lets staff in.
Grading and Sign-in need "change site settings"; Backups needs "view backup run" (asking for a
backup: "add backup run").
"""

import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin import site
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts import google
from accounts.models import User
from core import audit, crypto
from core.forms import GoogleClientForm, PauseWindowForm, TimeScaleForm
from core.models import BackupRun, PauseWindow, SiteSetting
from judging.internal import grading_paused
from judging.models import JudgeJob

CHANGE_SETTINGS = "core.change_sitesetting"


@permission_required(CHANGE_SETTINGS, raise_exception=True)
def grading_page(request: HttpRequest) -> HttpResponse:
    """Pause or resume grading now, plan pause windows (contest days), set the time scale."""
    setting = SiteSetting.get()
    window_form = PauseWindowForm()
    scale_form = TimeScaleForm(initial={"time_scale": setting.time_scale})
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("pause", "resume"):
            setting.grading_paused = action == "pause"
            setting.save(update_fields=["grading_paused"])
            audit.record(request, f"grading.{action}")
            return redirect("admin:grader-grading")
        if action == "add_window":
            window_form = PauseWindowForm(request.POST)
            if window_form.is_valid():
                window = window_form.save()
                details = {"id": window.pk, "window": str(window), "note": window.note}
                audit.record(request, "grading.window_add", window, details)
                return redirect("admin:grader-grading")
        elif action == "delete_window":
            window = get_object_or_404(PauseWindow, pk=request.POST.get("window", ""))
            details = {"id": window.pk, "window": str(window), "note": window.note}
            window.delete()
            audit.record(request, "grading.window_delete", details["window"], details)
            return redirect("admin:grader-grading")
        elif action == "time_scale":
            scale_form = TimeScaleForm(request.POST)
            if scale_form.is_valid():
                old, new = setting.time_scale, scale_form.cleaned_data["time_scale"]
                setting.time_scale = new
                setting.save(update_fields=["time_scale"])
                audit.record(request, "grading.time_scale", "", {"old": old, "new": new})
                messages.success(request, _("Time scale saved."))
                return redirect("admin:grader-grading")
    now = timezone.now()
    context = {
        **site.each_context(request),
        "title": _("Grading"),
        "setting": setting,
        "paused": grading_paused(),
        "windows": PauseWindow.objects.filter(ends_at__gt=now),
        "now": now,
        "queued": JudgeJob.objects.filter(status=JudgeJob.Status.QUEUED).count(),
        "window_form": window_form,
        "scale_form": scale_form,
    }
    return render(request, "admin/grader/grading.html", context)


# What the backup service writes: plain names ending in .dump (it writes "<name>.tmp" first).
BACKUP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.dump")
ACTIVE_BACKUPS = (BackupRun.Status.REQUESTED, BackupRun.Status.RUNNING)


@dataclass(frozen=True)
class BackupFile:
    name: str
    size: int
    modified: datetime


def list_backups(folder: Path) -> list[BackupFile]:
    """The backup files in `folder`, newest first. Symlinks and other files are left out."""
    files = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for entry in entries:
        if BACKUP_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
            info = entry.stat(follow_symlinks=False)
            modified = datetime.fromtimestamp(info.st_mtime, tz=UTC)
            files.append(BackupFile(entry.name, info.st_size, modified))
    return sorted(files, key=lambda file: file.modified, reverse=True)


@permission_required("core.view_backuprun", raise_exception=True)
def backups_page(request: HttpRequest) -> HttpResponse:
    """The backup files (download links), recent backup runs, and "back up now"."""
    if request.method == "POST":
        user = request.user
        if not isinstance(user, User) or not user.has_perm("core.add_backuprun"):
            raise PermissionDenied
        if request.POST.get("action") != "backup_now":
            raise PermissionDenied
        SiteSetting.get()  # make sure the row exists before locking it
        with transaction.atomic():
            # The settings row works as a lock: two clicks at once wait here for each other, so
            # only one of them finds no active backup and creates a request.
            SiteSetting.objects.select_for_update().get(pk=1)
            if not BackupRun.objects.filter(status__in=ACTIVE_BACKUPS).exists():
                run = BackupRun.objects.create(requested_by=user)
                audit.record(request, "backup.request", run)
        return redirect("admin:grader-backups")
    folder = Path(settings.GRADER_BACKUPS_DIR)
    context = {
        **site.each_context(request),
        "title": _("Backups"),
        "folder": folder,
        "folder_exists": folder.is_dir(),
        "files": list_backups(folder),
        "can_download": request.user.is_superuser,
        "runs": BackupRun.objects.select_related("requested_by").order_by("-requested_at")[:20],
    }
    return render(request, "admin/grader/backups.html", context)


@permission_required("core.view_backuprun", raise_exception=True)
def download_backup(request: HttpRequest, name: str) -> FileResponse:
    """One backup file, as a download. Only plain backup files directly in the folder.

    Superusers only: a dump holds every session and password hash, so whoever has it can become
    any user.
    """
    if not request.user.is_superuser:
        raise PermissionDenied
    if not BACKUP_NAME.fullmatch(name):
        raise Http404
    folder = Path(settings.GRADER_BACKUPS_DIR)
    try:
        # O_NOFOLLOW: a symlink in the folder is refused, never followed out of it.
        # O_NONBLOCK: a FIFO would otherwise make the open wait forever.
        descriptor = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Http404 from None
    file = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
        file.close()
        raise Http404
    audit.record(request, "backup.download", name)
    return FileResponse(file, as_attachment=True, filename=name)


@permission_required("core.change_sitesetting", raise_exception=True)
def signin_page(request: HttpRequest) -> HttpResponse:
    """The Google sign-in client: its ID, its write-only secret, and a check of the keys."""
    setting = SiteSetting.get()
    form = GoogleClientForm(initial={"client_id": setting.google_client_id})
    test: dict[str, object] = {}
    if request.method == "POST" and request.POST.get("action") == "save":
        form = GoogleClientForm(request.POST)
        if form.is_valid():
            setting.google_client_id = form.cleaned_data["client_id"]
            secret = form.cleaned_data["client_secret"]
            if secret:
                setting.google_client_secret_encrypted = crypto.encrypt_secret(secret)
            setting.save(update_fields=["google_client_id", "google_client_secret_encrypted"])
            details = {"client_id": setting.google_client_id, "secret_changed": bool(secret)}
            audit.record(request, "signin.save", "", details)
            messages.success(request, _("Saved."))
            return redirect("admin:grader-signin")
    elif request.method == "POST" and request.POST.get("action") == "test":
        test = _test_keys()
        audit.record(request, "signin.test", "", {"ok": test["ok"]})
    context = {
        **site.each_context(request),
        "title": _("Sign-in"),
        "form": form,
        "secret_set": bool(setting.google_client_secret_encrypted),
        "redirect_uri": f"{settings.PUBLIC_BASE_URL}/auth/google/callback",
        "keys_url": settings.GOOGLE_JWKS_URL,
        "test": test,
    }
    return render(request, "admin/grader/signin.html", context)


def _test_keys() -> dict[str, object]:
    """Fetch Google's signing keys the way a real sign-in does (through the edge's :8090)."""
    try:
        keys = google.fetch_signing_keys()
    except google.SignInError as error:
        return {"ok": False, "error": str(error)}
    count = len(keys.keys)
    if count == 0:
        return {"ok": False, "error": _("The answer holds no keys.")}
    return {"ok": True, "keys": count}
