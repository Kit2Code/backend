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
    os.environ.setdefault("GRADER_DB_HOST", "127.0.0.1")


def require(name: str) -> str:
    """Return the environment variable `name`, or stop if it is missing or empty."""
    value = os.environ.get(name, "")
    if not value:
        raise ImproperlyConfigured(f"Environment variable {name} must be set.")
    return value


def urlconf_for_role(role: str) -> str:
    """Return the URLconf module for a role ("public" or "admin")."""
    try:
        return ROLE_URLCONFS[role]
    except KeyError:
        allowed = ", ".join(ROLE_URLCONFS)
        raise ImproperlyConfigured(f"GRADER_ROLE must be one of: {allowed}.") from None


def read_secret(name: str) -> str:
    """Read one secret file from the directory in GRADER_SECRETS_DIR."""
    path = Path(require("GRADER_SECRETS_DIR")) / name
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
