"""Site settings, pause windows and the (read-only) audit log on the admin site."""

from django.contrib import admin
from django.http import HttpRequest

from config.admin import AuditedModelAdmin
from core.models import AuditLog, PauseWindow, SiteSetting


@admin.register(SiteSetting)
class SiteSettingAdmin(AuditedModelAdmin):
    # The Google client is set on the Sign-in page, where the secret stays write-only.
    fields = ("grading_paused", "time_scale", "registration_open", "announcement")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return not SiteSetting.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(PauseWindow)
class PauseWindowAdmin(AuditedModelAdmin):
    list_display = ("starts_at", "ends_at", "note")
    ordering = ("-starts_at",)


@admin.register(AuditLog)
class AuditLogAdmin(AuditedModelAdmin):
    """Rows are only ever added (by the code), never changed or removed by hand."""

    list_display = ("created_at", "actor_username", "action", "target", "ip")
    list_filter = ("action",)
    search_fields = ("actor_username", "action", "target")
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False
