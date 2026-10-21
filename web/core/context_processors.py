"""Values every template can use."""

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import get_language

from core.models import SiteSetting


def site(request: HttpRequest) -> dict[str, object]:
    """The site name in the current language, the role, and whether Google sign-in is set up."""
    name = settings.SITE_NAME_EN if get_language() == "en" else settings.SITE_NAME_TH
    signin_available = False
    if settings.GRADER_ROLE == "public":
        setting = SiteSetting.get()
        signin_available = bool(setting.google_client_id and setting.google_client_secret_encrypted)
    return {
        "site_name": name,
        "grader_role": settings.GRADER_ROLE,
        "signin_available": signin_available,
    }
