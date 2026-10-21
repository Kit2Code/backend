"""Our Django admin site: the stock one, with the staff login rate-limited.

Installed through `GraderAdminConfig` (in INSTALLED_APPS instead of "django.contrib.admin"), so
`admin.site` and every `@admin.register` use this site.
"""

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _

from core import ratelimit


class GraderAdminSite(admin.AdminSite):
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
