"""Google profile photos: fetched once per sign-in, kept in the database, served by our own site.

Visitors' browsers never contact Google (the CSP allows images from this site only). The web role
fetches the photo through the edge's :8090 /photo route, which forwards only to
lh3.googleusercontent.com. We accept only:
- a `picture` address on https://lh3.googleusercontent.com with a plain path (letters, digits,
  `_` and `-` in "/"-separated parts), asked for at 256 px (Google's `=s256-c` size suffix),
- at most MAX_PHOTO_BYTES, with no redirects followed,
- bytes that start like a JPEG or PNG file (whatever the Content-Type header says).
Any failure keeps the photo we already have and never stops the sign-in.
"""

import logging
import re
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import Avatar, User

logger = logging.getLogger(__name__)

PHOTO_HOST = "lh3.googleusercontent.com"
SIZE_SUFFIX = "=s256-c"
MAX_PHOTO_BYTES = 256 * 1024
MAX_PATH_LENGTH = 512
HTTP_TIMEOUT_S = 10
# A path of plain parts, then Google's optional "=<options>" suffix, which we replace.
PHOTO_PATH = re.compile(r"((?:/[A-Za-z0-9_-]+)+)(=[A-Za-z0-9-]+)?")
# File signatures: the first bytes of every JPEG and PNG file.
SIGNATURES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


def photo_path(picture: str) -> str | None:
    """The path to ask the edge for (at 256 px), or None if `picture` isn't a Google photo."""
    if len(picture) > MAX_PATH_LENGTH:
        return None
    try:
        parts = urlsplit(picture)
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or parts.hostname != PHOTO_HOST
        or parts.netloc != PHOTO_HOST  # no user name, password or port
        or port is not None
        or parts.query
        or parts.fragment
    ):
        return None
    match = PHOTO_PATH.fullmatch(parts.path)
    if match is None:
        return None
    return match.group(1) + SIZE_SUFFIX


def image_type(data: bytes) -> str | None:
    """ "image/jpeg" or "image/png" from the file's first bytes, or None."""
    for signature, content_type in SIGNATURES.items():
        if data.startswith(signature):
            return content_type
    return None


def fetch(path: str) -> tuple[bytes, str] | None:
    """Download the photo at `path` through the edge; return (bytes, content type) or None."""
    with requests.Session() as http:
        http.trust_env = False  # talk to the edge directly, whatever the environment says
        try:
            response = http.get(
                settings.GOOGLE_PHOTO_URL + path,
                timeout=HTTP_TIMEOUT_S,
                stream=True,
                allow_redirects=False,
            )
            with response:
                if response.status_code != 200:
                    logger.info("profile photo request answered %s", response.status_code)
                    return None
                data = response.raw.read(MAX_PHOTO_BYTES + 1, decode_content=True)
        except requests.RequestException as error:
            logger.info("profile photo request failed: %s", type(error).__name__)
            return None
    if len(data) > MAX_PHOTO_BYTES:
        logger.info("profile photo is over %s bytes", MAX_PHOTO_BYTES)
        return None
    content_type = image_type(data)
    if content_type is None:
        logger.info("profile photo is not a JPEG or PNG")
        return None
    return bytes(data), content_type


def refresh(user: User, picture: str) -> None:
    """At sign-in: fetch and keep `user`'s Google photo, unless they switched photos off."""
    if not user.show_photo:
        return
    path = photo_path(picture)
    if path is None:
        return
    photo = fetch(path)
    if photo is None:
        return
    data, content_type = photo
    with transaction.atomic():
        Avatar.objects.update_or_create(
            user=user, defaults={"image": data, "content_type": content_type}
        )
        User.objects.filter(pk=user.pk).update(photo_updated_at=timezone.now())


def forget(user: User) -> None:
    """Delete `user`'s stored photo."""
    with transaction.atomic():
        Avatar.objects.filter(user=user).delete()
        User.objects.filter(pk=user.pk).update(photo_updated_at=None)
