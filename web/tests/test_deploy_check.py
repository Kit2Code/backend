"""Django's deployment checks pass for both roles, with nothing left at WARNING level.

Each role's settings are fixed when the process starts, so each check runs in its own process.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]


def deploy_check(role: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv list
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=WEB,
        env={**os.environ, "GRADER_ROLE": role},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("role", ["public", "admin"])
def test_deploy_check_is_clean(role: str) -> None:
    result = deploy_check(role)
    assert result.returncode == 0, result.stdout + result.stderr


def role_setting(role: str, expression: str) -> str:
    code = f"import django; django.setup(); from django.conf import settings; print(({expression}))"
    result = subprocess.run(  # noqa: S603 - fixed argv list
        [sys.executable, "-c", code],
        cwd=WEB,
        env={**os.environ, "GRADER_ROLE": role, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_public_cookies_are_secure_and_hsts_is_on() -> None:
    expression = (
        "settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE,"
        " settings.LANGUAGE_COOKIE_SECURE, settings.SECURE_HSTS_SECONDS >= 31536000,"
        " settings.SECURE_PROXY_SSL_HEADER"
    )
    assert role_setting("public", expression) == (
        "(True, True, True, True, ('HTTP_X_FORWARDED_PROTO', 'https'))"
    )


def test_admin_role_is_plain_http_on_the_lan() -> None:
    # The admin site is reached over plain HTTP on the home network: Secure cookies would never
    # be sent back, and the proxy header must not be trusted there.
    expression = (
        "settings.SESSION_COOKIE_SECURE, settings.SECURE_HSTS_SECONDS,"
        " settings.SECURE_PROXY_SSL_HEADER"
    )
    assert role_setting("admin", expression) == "(False, 0, None)"
