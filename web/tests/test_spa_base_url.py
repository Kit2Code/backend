"""GRADER_SPA_BASE_URL (development only): the React dev server's address is accepted by the public
role, and the SPA's Google sign-in comes back to it."""

import json
import os
import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
PRINT = (
    "import json, django; from django.conf import settings; django.setup(); print(json.dumps("
    "[settings.ALLOWED_HOSTS, settings.CSRF_TRUSTED_ORIGINS, settings.GOOGLE_API_REDIRECT_URI,"
    " settings.GOOGLE_REDIRECT_URI]))"
)


def settings_with(role: str, spa: str | None) -> list[object]:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", "GRADER_ROLE": role}
    env.pop("GRADER_SPA_BASE_URL", None)
    if spa is not None:
        env["GRADER_SPA_BASE_URL"] = spa
    out = subprocess.run(  # noqa: S603 - our own interpreter and code
        [sys.executable, "-c", PRINT], cwd=WEB, env=env, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


def test_unset_changes_nothing() -> None:
    _hosts, origins, api_redirect, _old = settings_with("public", None)
    assert "http://localhost:5173" not in origins  # type: ignore[operator]
    assert not str(api_redirect).startswith("http://localhost:5173")


def test_set_adds_the_dev_server_and_moves_only_the_api_callback() -> None:
    hosts, origins, api_redirect, old_redirect = settings_with("public", "http://localhost:5173")
    assert "localhost" in hosts  # type: ignore[operator]
    assert "http://localhost:5173" in origins  # type: ignore[operator]
    assert api_redirect == "http://localhost:5173/api/auth/google/callback"
    assert not str(old_redirect).startswith("http://localhost:5173")  # old pages unchanged


def test_ignored_by_the_admin_role() -> None:
    hosts, origins, _api, _old = settings_with("admin", "http://localhost:5173")
    assert "http://localhost:5173" not in origins  # type: ignore[operator]
