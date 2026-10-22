"""Problems and tags on the admin site. Publishing goes through actions, so it can be checked."""

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from config.admin import AuditedModelAdmin
from core import audit
from problems.models import Problem, ProblemVersion, Tag


@admin.register(Problem)
class ProblemAdmin(AuditedModelAdmin):
    list_display = ("slug", "title_th", "title_en", "is_visible", "current_version", "status")
    list_filter = ("is_visible", "tags")
    search_fields = ("slug", "title_th", "title_en")
    list_select_related = ("current_version",)
    filter_horizontal = ("tags",)
    # Visibility and the current version change only through publish/hide and the import page.
    readonly_fields = ("slug", "is_visible", "current_version", "created_at", "updated_at")
    fields = ("slug", "title_th", "title_en", "tags", *readonly_fields[1:])
    actions = ("publish", "hide")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False  # problems come from the import page

    @admin.display(description=_("validation"))
    def status(self, problem: Problem) -> str:
        version = problem.current_version
        return version.get_status_display() if version else "-"

    @admin.action(description=_("Publish the selected problems"), permissions=["change"])
    def publish(self, request: HttpRequest, queryset: QuerySet[Problem]) -> None:
        refused = []
        for problem in queryset.select_related("current_version"):
            version = problem.current_version
            if version is None or version.status != ProblemVersion.Status.VALID:
                refused.append(problem.slug)
                continue
            if not problem.is_visible:
                problem.is_visible = True
                problem.save(update_fields=["is_visible", "updated_at"])
                audit.record(request, "problem.publish", problem, {"version": version.dir_name})
        if refused:
            message = _("Not published (the current version has not passed validation): %(slugs)s")
            self.message_user(request, message % {"slugs": ", ".join(refused)}, messages.WARNING)

    @admin.action(description=_("Hide the selected problems"), permissions=["change"])
    def hide(self, request: HttpRequest, queryset: QuerySet[Problem]) -> None:
        for problem in queryset.filter(is_visible=True):
            problem.is_visible = False
            problem.save(update_fields=["is_visible", "updated_at"])
            audit.record(request, "problem.hide", problem)


@admin.register(Tag)
class TagAdmin(AuditedModelAdmin):
    search_fields = ("name",)
