"""The problem store: immutable version folders `<store>/<slug>/v<NNN>-<hash8>/`.

A version is never changed after it is created. Re-importing different content creates the next
version; re-importing the same content finds the existing one (the hash is the same). New versions
are assembled in `<store>/.staging/` on the same filesystem and then renamed into place in one
step, so nobody ever sees a half-written version.
"""

import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REPORT_PATH = "meta/import-report.json"
VERSION_RE = re.compile(r"v(\d{3,})-([0-9a-f]{8})")
_CHUNK = 1 << 20


def content_hash(folder: Path) -> str:
    """SHA-256 over every file's relative path, size and bytes, in sorted order.

    The import report is left out: it describes the import (time, warnings), not the problem.
    """
    files = sorted(
        (path.relative_to(folder).as_posix(), path) for path in folder.rglob("*") if path.is_file()
    )
    digest = hashlib.sha256()
    for name, path in files:
        if name == REPORT_PATH:
            continue
        encoded = name.encode("utf-8", "surrogateescape")
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as f:
            while chunk := f.read(_CHUNK):
                digest.update(chunk)
    return digest.hexdigest()


def find_existing(store: Path, slug: str, digest: str) -> Path | None:
    """Return the stored version with exactly this content hash, if there is one."""
    for version in _versions(store / slug):
        if version.name.endswith("-" + digest[:8]) and _recorded_hash(version) == digest:
            return version
    return None


def store_version(staged: Path, store: Path, slug: str, digest: str) -> Path:
    """Move the finished folder `staged` into the store as the next version of `slug`."""
    slug_dir = store / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    number = 1 + max((int(m.group(1)) for m in _version_matches(slug_dir)), default=0)
    while True:
        target = slug_dir / f"v{number:03d}-{digest[:8]}"
        try:
            os.rename(staged, target)
        except OSError as error:
            # Another import took this number first: try the next one.
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                number += 1
                continue
            raise
        return target


@contextmanager
def staging_area(store: Path) -> Iterator[Path]:
    """A fresh private folder under `<store>/.staging/`, deleted when the block ends."""
    root = store / ".staging"
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(dir=root))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _versions(slug_dir: Path) -> list[Path]:
    return [slug_dir / m.group(0) for m in _version_matches(slug_dir)]


def _version_matches(slug_dir: Path) -> list[re.Match[str]]:
    if not slug_dir.is_dir():
        return []
    matches = (VERSION_RE.fullmatch(entry.name) for entry in slug_dir.iterdir())
    return [m for m in matches if m is not None]


def _recorded_hash(version: Path) -> str | None:
    try:
        report = json.loads((version / REPORT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = report.get("content_sha256") if isinstance(report, dict) else None
    return value if isinstance(value, str) else None
