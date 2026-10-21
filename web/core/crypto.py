"""Encrypting the few secrets we keep in the database (today: the Google client secret).

The key is derived from Django's SECRET_KEY with HKDF, so a database backup alone can't reveal
the secret; restoring onto a new install means typing the secret again (docs/DECISIONS.md).
"""

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings


class DecryptError(Exception):
    """The value was not made by encrypt_secret with this SECRET_KEY (or was changed)."""


def _fernet() -> Fernet:
    # HKDF turns the long SECRET_KEY into a 32-byte key used for this purpose only ("info").
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"grader:db-secrets:v1")
    key = hkdf.derive(settings.SECRET_KEY.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    """Encrypt and sign `plain`; the result is safe to store as text."""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Undo encrypt_secret. Raises DecryptError if the value is damaged or from another key."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as error:
        raise DecryptError from error
