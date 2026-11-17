"""What every `/api/*` request gets (PLAN.md §5.1, §5.4): the `?lang=` language, UTC times,
a body-size limit before anything reads the body, and `Cache-Control: no-store` by default."""

import re
from datetime import UTC

from django.http import HttpRequest, JsonResponse
from django.http.response import HttpResponseBase
from django.utils import timezone, translation

from api.errors import body
from core.middleware import GetResponse

SUBMIT_PATH = re.compile(r"^/api/problems/[^/]+/submissions$")
MAX_SUBMIT_BODY_BYTES = 256 * 1024
MAX_BODY_BYTES = 16 * 1024
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class ApiMiddleware:
    """Goes after LocaleMiddleware (so an old `g_lang` cookie never reaches the API) and before
    anything that reads the body."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if not request.path.startswith("/api/"):
            return self.get_response(request)
        if request.method not in SAFE_METHODS and _too_big(request):
            response: HttpResponseBase = JsonResponse(
                body("payload_too_large", "The request is too big."), status=413
            )
        else:
            language = "en" if request.GET.get("lang") == "en" else "th"
            translation.activate(language)
            request.LANGUAGE_CODE = language
            timezone.activate(UTC)  # DRF then writes times as "…Z" (contract §3)
            try:
                response = self.get_response(request)
            finally:
                timezone.deactivate()  # the thread serves old pages next, in Bangkok time
        if not response.has_header("Cache-Control"):
            response["Cache-Control"] = "no-store"  # answers depend on who is asking
        return response


def _too_big(request: HttpRequest) -> bool:
    # shortcut: a chunked body (no Content-Length) passes here; add PLAN.md §5.4's WSGI de-chunker
    # before the submit endpoint (4.1) if the edge or tunnel can forward one.
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return True
    limit = MAX_SUBMIT_BODY_BYTES if SUBMIT_PATH.match(request.path) else MAX_BODY_BYTES
    return length > limit
