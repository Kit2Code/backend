"""Read the settings that come from the environment: the role, base URLs and secret files.

Every container gets its role and URLs as environment variables and its secrets as files in a
mounted directory (see docs/ARCHITECTURE.md, "Secrets"). Anything missing or invalid stops the
process at startup with a clear message instead of running half-configured.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

ROLE_URLCONFS = {
    "public": "config.urls_public",
    "admin": "config.urls_admin",
}

# Each role has its own cookie names, so a browser never sends one role's session to the other.
ROLE_COOKIE_NAMES = {
    "public": {"SESSION_COOKIE_NAME": "g_sess", "CSRF_COOKIE_NAME": "g_csrf"},
    "admin": {"SESSION_COOKIE_NAME": "g_admin", "CSRF_COOKIE_NAME": "g_admin_csrf"},
}

# The repo root when running from a checkout (web/config/env.py -> repo).
REPO_ROOT = Path(__file__).resolve().parents[2]


def apply_dev_defaults() -> None:
    """Fill in defaults for running manage.py, pytest or mypy on a developer machine.

    This only does something when `.devdata/` (made by ./init.sh) exists next to the code.
    Images never contain `.devdata/`, so in containers every value must be set explicitly.
    """
    devdata = REPO_ROOT / ".devdata"
    if not devdata.is_dir():
        return
    os.environ.setdefault("GRADER_ROLE", "admin")
    os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    os.environ.setdefault("ADMIN_BASE_URL", "http://127.0.0.1:8001")
    os.environ.setdefault("GRADER_SECRETS_DIR", str(devdata / "secrets" / "web"))
    os.environ.setdefault("GRADER_WORKER_SECRETS_DIR", str(devdata / "secrets" / "worker"))
    os.environ.setdefault("GRADER_DB_HOST", "127.0.0.1")
    os.environ.setdefault("GRADER_STATIC_FROM_APPS", "1")
    os.environ.setdefault("GRADER_PROBLEMS_DIR", str(devdata / "problems"))
    os.environ.setdefault("GRADER_UPLOADS_DIR", str(devdata / "uploads"))
    os.environ.setdefault("GRADER_BACKUPS_DIR", str(devdata / "backups"))


def require(name: str) -> str:
    """Return the environment variable `name`, or stop if it is missing or empty."""
    value = os.environ.get(name, "")
    if not value:
        raise ImproperlyConfigured(f"Environment variable {name} must be set.")
    return value


def optional(name: str, default: str) -> str:
    """Return the environment variable `name`, or `default` if it is missing or empty."""
    return os.environ.get(name) or default


def urlconf_for_role(role: str) -> str:
    """Return the URLconf module for a role ("public" or "admin")."""
    try:
        return ROLE_URLCONFS[role]
    except KeyError:
        allowed = ", ".join(ROLE_URLCONFS)
        raise ImproperlyConfigured(f"GRADER_ROLE must be one of: {allowed}.") from None


def role_cookie_names(role: str) -> dict[str, str]:
    """Return the session and CSRF cookie name settings for a role."""
    return dict(ROLE_COOKIE_NAMES[role])


def read_secret(name: str, folder_variable: str = "GRADER_SECRETS_DIR") -> str:
    """Read one secret file from the directory named by the environment variable `folder_variable`.

    Most secrets are in the web roles' own folder (GRADER_SECRETS_DIR). The admin role also reads
    the worker token from the worker's folder (GRADER_WORKER_SECRETS_DIR), like production.
    """
    path = Path(require(folder_variable)) / name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ImproperlyConfigured(f"Cannot read secret file {path}: {error.strerror}") from None
    if not value:
        raise ImproperlyConfigured(f"Secret file {path} is empty.")
    return value


def base_url_parts(url: str) -> tuple[str, str]:
    """Split a base URL like "https://grader.example.com" into (hostname, origin)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ImproperlyConfigured(f"Base URL {url!r} must look like https://host[:port].")
    return parts.hostname, f"{parts.scheme}://{parts.netloc}"


def extra_base_urls(value: str) -> list[tuple[str, str]]:
    """Comma-separated extra base URLs (for example the admin site's Tailscale address), each as
    (hostname, origin). Empty items are ignored."""
    urls = [base_url_parts(url.strip()) for url in value.split(",") if url.strip()]
    for hostname, _origin in urls:
        # Django reads "*" or ".example.org" in ALLOWED_HOSTS as wildcards: never from here.
        if "*" in hostname or hostname.startswith("."):
            raise ImproperlyConfigured(f"{hostname!r}: give exact addresses, without wildcards.")
    return urls
