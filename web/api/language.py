"""The API language: `?lang=en` or Thai (the contract's default)."""

from rest_framework.request import Request


def api_language(request: Request) -> str:
    return "en" if request.query_params.get("lang") == "en" else "th"
