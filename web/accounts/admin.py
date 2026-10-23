"""Users and groups on the admin site. Staff disable or enable accounts with actions (audited)."""

from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from accounts.models import GoogleIdentity, User
from config.admin import AuditedModelAdmin
from core import audit


@admin.register(User)
class UserAdmin(AuditedModelAdmin, DjangoUserAdmin):  # type: ignore[type-arg]
    list_display = ("username", "display_name", "google_linked", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "display_name")
    list_select_related = ("google_identity",)
    fieldsets = (
        *(DjangoUserAdmin.fieldsets or ()),
        (_("Profile"), {"fields": ("display_name",)}),
    )
    actions = ("disable", "enable", "unlink_google")

    @admin.display(boolean=True, description=_("Google"))
    def google_linked(self, user: User) -> bool:
        return hasattr(user, "google_identity")

    @admin.action(description=_("Disable the selected users"), permissions=["change"])
    def disable(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        self._set_active(request, queryset, active=False)

    @admin.action(description=_("Enable the selected users"), permissions=["change"])
    def enable(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        self._set_active(request, queryset, active=True)

    @admin.action(description=_("Unlink their Google accounts"), permissions=["change"])
    def unlink_google(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        """For someone who lost their Google account: the link goes, the user and history stay."""
        for identity in GoogleIdentity.objects.filter(user__in=queryset).select_related("user"):
            identity.delete()
            audit.record(request, "user.unlink_google", identity.user, {"issuer": identity.issuer})
        self.message_user(request, _("Done."), messages.SUCCESS)

    def _set_active(self, request: HttpRequest, queryset: QuerySet[User], *, active: bool) -> None:
        action = "user.enable" if active else "user.disable"
        for user in queryset.exclude(is_active=active):
            user.is_active = active
            user.save(update_fields=["is_active"])
            audit.record(request, action, user)
        self.message_user(request, _("Done."), messages.SUCCESS)


admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(AuditedModelAdmin, DjangoGroupAdmin):
    pass
