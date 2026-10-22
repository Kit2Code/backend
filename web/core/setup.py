"""First-run setup: a one-time link that creates the first superuser.

While no superuser exists, every start of web-admin makes a new random token, stores only its
SHA-256 (so a database backup can't be used to take over the site), prints the link to the log
and writes it to `<uploads>/SETUP-LINK.txt`. The link works once, for 24 hours, and only while
there is still no superuser. See docs/ARCHITECTURE.md, "First-run setup".
"""

import hashlib
import os
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from core.models import SetupToken

LINK_FILE_NAME = "SETUP-LINK.txt"
LIFETIME = timedelta(hours=24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def needs_setup() -> bool:
    """True while there is no superuser yet."""
    return not User.objects.filter(is_superuser=True).exists()


def link_file() -> Path:
    return Path(settings.GRADER_UPLOADS_DIR) / LINK_FILE_NAME


def issue_link() -> str | None:
    """Make a fresh setup link, replacing any earlier one. Save it with `write_link_file`.

    Returns the link, or None when a superuser exists (then the file and old tokens are removed).
    """
    with transaction.atomic():
        SetupToken.objects.filter(used_at__isnull=True).delete()
        if not needs_setup():
            link_file().unlink(missing_ok=True)
            return None
        token = secrets.token_urlsafe(32)
        SetupToken.objects.create(
            token_hash=hash_token(token), expires_at=timezone.now() + LIFETIME
        )
    return f"{settings.BASE_URL}/setup?token={token}"


def write_link_file(link: str) -> None:
    """Write the link next to the uploads, readable only by its owner; replace it atomically."""
    path = link_file()
    temporary = path.with_name(path.name + ".tmp")
    # O_EXCL after removing a leftover: never write through a file or link someone planted.
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(link + "\n")
    temporary.replace(path)


def usable_token(token_hash: str, *, lock: bool = False) -> SetupToken | None:
    """The stored token with this hash, if it is unused, unexpired and setup is still needed.

    Callers pass the hash (`hash_token`), so not even the session ever holds the token itself.
    With `lock=True` (inside a transaction) the row stays locked, so two forms sent at the same
    moment can't both use it.
    """
    if not token_hash:
        return None
    rows = SetupToken.objects.select_for_update() if lock else SetupToken.objects.all()
    row = rows.filter(
        token_hash=token_hash, used_at__isnull=True, expires_at__gt=timezone.now()
    ).first()
    if row is None or not needs_setup():
        return None
    return row


def create_first_superuser(token_hash: str, username: str, password: str) -> User | None:
    """Use the token to create the first superuser. None if it is (no longer) usable."""
    with transaction.atomic():
        row = usable_token(token_hash, lock=True)
        if row is None:
            return None
        user = User.objects.create_superuser(username=username, password=password)
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
    link_file().unlink(missing_ok=True)
    return user
