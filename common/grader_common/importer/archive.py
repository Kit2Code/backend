"""Unpack an uploaded zip, or copy an uploaded folder, without trusting anything inside it.

Uploads are hostile until proven otherwise. A zip can contain names like `../../etc/x` or
`/etc/x`, symlinks pointing anywhere, device files, a million entries, or a few kilobytes that
unpack to many gigabytes (a "zip bomb"). Everything here refuses those, checks sizes both from the
zip headers and again while writing (headers can lie), creates files only with `O_EXCL | O_NOFOLLOW`
so nothing is ever written through a link, and removes the half-written output on any error.
"""

import errno
import os
import shutil
import stat
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from grader_common.importer.errors import ProblemImportError

_CHUNK = 1 << 20
_JUNK_FOLDERS = {"__MACOSX"}
_JUNK_FILES = {".DS_Store"}


@dataclass(frozen=True)
class Limits:
    """How much an upload may contain."""

    max_files: int = 10_000
    max_total_bytes: int = 1 << 30  # 1 GB unpacked
    max_file_bytes: int = 256 << 20  # 256 MB for any single file


DEFAULT_LIMITS = Limits()


class _Budget:
    """Counts files and bytes against the limits while unpacking."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.files = 0
        self.total = 0

    def add_file(self) -> None:
        self.files += 1
        if self.files > self.limits.max_files:
            raise ProblemImportError(f"too many files (more than {self.limits.max_files})")

    def add_bytes(self, name: str, file_bytes: int, more: int) -> None:
        if file_bytes > self.limits.max_file_bytes:
            raise ProblemImportError(
                f"{name}: file is larger than {self.limits.max_file_bytes} bytes"
            )
        self.total += more
        if self.total > self.limits.max_total_bytes:
            raise ProblemImportError(
                f"upload unpacks to more than {self.limits.max_total_bytes} bytes"
            )


def extract_zip(zip_path: Path, dest: Path, limits: Limits = DEFAULT_LIMITS) -> None:
    """Unpack `zip_path` into the new folder `dest` (which must not exist yet).

    Raises ProblemImportError if anything is unsafe or over the limits; `dest` is then removed.
    """
    with _cleanup_on_error(dest):
        try:
            with zipfile.ZipFile(zip_path) as archive:
                members = _checked_members(archive, limits)
                dest.mkdir()
                budget = _Budget(limits)
                for info, parts in members:
                    budget.add_file()
                    target = _prepare_target(dest, parts, is_dir=info.is_dir())
                    if not info.is_dir():
                        with archive.open(info) as source:
                            _write_file(source, target, "/".join(parts), budget)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as error:
            raise ProblemImportError(f"not a valid zip file: {error}") from None


def copy_tree(src: Path, dest: Path, limits: Limits = DEFAULT_LIMITS) -> None:
    """Copy the folder `src` into the new folder `dest`, refusing links and special files."""
    with _cleanup_on_error(dest):
        if stat.S_ISLNK(os.lstat(src).st_mode):
            raise ProblemImportError(f"{src.name}: the folder is a symlink")
        if not src.is_dir():
            raise ProblemImportError(f"{src}: not a folder")
        dest.mkdir()
        _copy_dir(src, dest, (), _Budget(limits))


def unwrap_single_folder(root: Path) -> Path:
    """If `root` holds exactly one folder and nothing else, return that folder (zips often do)."""
    entries = list(root.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return root


# --- helpers -------------------------------------------------------------------------------


@contextmanager
def _cleanup_on_error(dest: Path) -> Iterator[None]:
    """Run the block; if it fails, delete `dest` (if we created it) and re-raise.

    OS errors (disk full, a name clash) become a ProblemImportError with a short message.
    """
    existed = dest.exists()
    try:
        yield
    except BaseException as error:
        if not existed and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if isinstance(error, OSError) and not isinstance(error, ProblemImportError):
            name = Path(error.filename).name if error.filename else "upload"
            raise ProblemImportError(f"{name}: {error.strerror}") from None
        raise


def _checked_members(
    archive: zipfile.ZipFile, limits: Limits
) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
    """Check every entry's name, type and declared size before anything is written."""
    members = []
    seen: set[tuple[str, ...]] = set()
    total = 0
    for info in archive.infolist():
        parts = _safe_parts(info.filename)
        if _is_junk(parts):
            continue
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type == stat.S_IFLNK:
            raise ProblemImportError(f"{info.filename}: symlinks are not allowed")
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ProblemImportError(f"{info.filename}: special files are not allowed")
        if info.flag_bits & 0x1:
            raise ProblemImportError(f"{info.filename}: encrypted files are not allowed")
        if parts in seen:
            raise ProblemImportError(f"{info.filename}: the name appears twice")
        seen.add(parts)
        if not info.is_dir():
            if info.file_size > limits.max_file_bytes:
                raise ProblemImportError(
                    f"{info.filename}: file is larger than {limits.max_file_bytes} bytes"
                )
            total += info.file_size
        members.append((info, parts))
    if len(members) > limits.max_files:
        raise ProblemImportError(f"too many files (more than {limits.max_files})")
    if total > limits.max_total_bytes:
        raise ProblemImportError(f"upload unpacks to more than {limits.max_total_bytes} bytes")
    return members


def _safe_parts(name: str) -> tuple[str, ...]:
    """Split a zip entry name into folder parts, refusing anything that could escape `dest`."""
    trimmed = name[:-1] if name.endswith("/") else name
    parts = tuple(trimmed.split("/"))
    if trimmed.startswith("/") or not all(_is_safe_part(part) for part in parts):
        raise ProblemImportError(f"{name!r}: unsafe file name")
    return parts


def _is_safe_part(part: str) -> bool:
    return (
        part not in ("", ".", "..")
        and len(part.encode("utf-8", "surrogateescape")) <= 255
        and not any(char in part for char in "\\:")
        and all(ord(char) >= 0x20 for char in part)
    )


def _is_junk(parts: tuple[str, ...]) -> bool:
    """Files that macOS adds to every zip it makes."""
    return parts[0] in _JUNK_FOLDERS or parts[-1] in _JUNK_FILES


def _prepare_target(dest: Path, parts: tuple[str, ...], is_dir: bool) -> Path:
    """Create the parent folders of an entry and return its path, which must stay inside `dest`."""
    target = dest.joinpath(*parts)
    folder = target if is_dir else target.parent
    folder.mkdir(parents=True, exist_ok=True)
    real_dest = os.path.realpath(dest)
    if os.path.commonpath([real_dest, os.path.realpath(folder)]) != real_dest:
        raise ProblemImportError(f"{'/'.join(parts)}: path leaves the upload folder")
    return target


def _write_file(source: IO[bytes], target: Path, name: str, budget: _Budget) -> None:
    """Copy `source` into the new file `target`, counting bytes against the limits as we go."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(target, flags, 0o644)
    except FileExistsError:
        raise ProblemImportError(f"{name}: the name appears twice") from None
    written = 0
    with os.fdopen(fd, "wb") as out:
        while chunk := source.read(_CHUNK):
            written += len(chunk)
            budget.add_bytes(name, written, len(chunk))
            out.write(chunk)


def _copy_dir(src: Path, dest: Path, prefix: tuple[str, ...], budget: _Budget) -> None:
    """Copy one folder level, then recurse. Never follows symlinks."""
    with os.scandir(src) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            parts = (*prefix, entry.name)
            name = "/".join(parts)
            if not _is_safe_part(entry.name):
                raise ProblemImportError(f"{name!r}: unsafe file name")
            if _is_junk(parts):
                continue
            if entry.is_symlink():
                raise ProblemImportError(f"{name}: symlinks are not allowed")
            budget.add_file()
            if entry.is_dir(follow_symlinks=False):
                (dest / entry.name).mkdir()
                _copy_dir(Path(entry.path), dest / entry.name, parts, budget)
            elif entry.is_file(follow_symlinks=False):
                _copy_file(Path(entry.path), dest / entry.name, name, budget)
            else:
                raise ProblemImportError(f"{name}: special files are not allowed")


def _copy_file(path: Path, target: Path, name: str, budget: _Budget) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ProblemImportError(f"{name}: symlinks are not allowed") from None
        raise
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ProblemImportError(f"{name}: special files are not allowed")
        _write_file(source, target, name, budget)
