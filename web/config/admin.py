"""Our Django admin site: the stock one, with the staff login rate-limited and every edit audited.

Installed through `GraderAdminConfig` (in INSTALLED_APPS instead of "django.contrib.admin"), so
`admin.site` and every `@admin.register` use this site.
"""

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.db.models import Model
from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, path
from django.utils.translation import gettext as _

from core import ratelimit

if TYPE_CHECKING:
    # Only for type hints: this module is loaded from INSTALLED_APPS, before models may be imported.
    from django.contrib.admin.models import LogEntry


class AuditedModelAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """A ModelAdmin whose adds, changes and deletes also write our AuditLog (with the IP).

    Django calls these three methods for every edit made through the admin, including the
    "delete selected" action. Every model on our admin site uses this class (a test checks).
    """

    def log_addition(self, request: HttpRequest, obj: Model, message: Any) -> "LogEntry":
        entry = super().log_addition(request, obj, message)
        _record_edit(request, "admin.add", obj, entry.change_message)
        return entry

    def log_change(self, request: HttpRequest, obj: Model, message: Any) -> "LogEntry":
        entry = super().log_change(request, obj, message)
        _record_edit(request, "admin.change", obj, entry.change_message)
        return entry

    def log_deletions(self, request: HttpRequest, queryset: Any) -> Any:
        # Called just before the objects are deleted, so they can still be described.
        for obj in queryset:
            _record_edit(request, "admin.delete", obj, "")
        return super().log_deletions(request, queryset)


def _record_edit(request: HttpRequest, action: str, obj: Model, message: str) -> None:
    """`message` is Django's change message as stored (JSON, not translated), like the admin's
    own history: the audit log reads the same whatever language the viewer uses."""
    from core import audit  # here, not at the top: see the TYPE_CHECKING note above

    details = {"model": obj._meta.label_lower, "id": obj.pk, "message": message}
    audit.record(request, action, obj, details)


class GraderAdminSite(admin.AdminSite):
    # Our index adds a box with links to the pages below.
    index_template = "admin/grader/index.html"

    def get_urls(self) -> list[URLPattern | URLResolver]:
        """Django admin's URLs plus our own pages, all staff-only through `admin_view`."""
        # Imported here: their models can't be loaded when this module is (see TYPE_CHECKING).
        from core import admin_views as core
        from judging import admin_views as judging
        from problems import admin_views as problems

        pages = [
            path("grader/grading/", self.admin_view(core.grading_page), name="grader-grading"),
            path("grader/backups/", self.admin_view(core.backups_page), name="grader-backups"),
            path("grader/signin/", self.admin_view(core.signin_page), name="grader-signin"),
            path("grader/backups/<str:name>", self.admin_view(core.download_backup),
                 name="grader-backup-file"),
            path("grader/system/", self.admin_view(judging.system_page), name="grader-system"),
            path("grader/calibrate/", self.admin_view(judging.calibrate_page),
                 name="grader-calibrate"),
            path("grader/import/", self.admin_view(problems.import_page), name="grader-import"),
            path("grader/versions/<int:pk>/", self.admin_view(problems.version_page),
                 name="grader-version"),
            path("grader/versions/<int:pk>/publish", self.admin_view(problems.publish_version),
                 name="grader-publish"),
            path("grader/versions/<int:pk>/validate", self.admin_view(problems.validate_version),
                 name="grader-validate"),
        ]  # fmt: skip
        return pages + super().get_urls()

    def login(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> HttpResponse:
        """The admin login page; each POST counts against the limit for this address + username."""
        if request.method == "POST":
            username = request.POST.get("username", "").casefold()
            key = f"{ratelimit.client_ip(request)}|{username}"
            if not ratelimit.hit(ratelimit.STAFF_LOGIN, key):
                return HttpResponse(
                    _("Too many sign-in attempts. Please wait 15 minutes and try again."),
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )
        return super().login(request, extra_context)


class GraderAdminConfig(AdminConfig):
    default_site = "config.admin.GraderAdminSite"
