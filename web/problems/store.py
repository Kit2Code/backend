"""Reading problem versions from the problem store (`<store>/<slug>/<version>/`).

Versions never change after they are created, so what we read from one can be cached for as long
as the process lives.
"""

import errno
import os
import stat
from functools import lru_cache
from pathlib import Path, PurePosixPath

from django.conf import settings

from grader_common.importer.native import load_problem
from grader_common.problem_format import Problem as ProblemFormat
from problems.models import ProblemVersion


class NotInStore(Exception):
    """The file isn't there, isn't a regular file, or lies outside the version folder."""


def version_path(version: ProblemVersion) -> Path:
    """The version's folder in the store."""
    return Path(settings.GRADER_PROBLEMS_DIR) / version.problem.slug / version.dir_name


@lru_cache(maxsize=256)
def _load(folder: Path) -> ProblemFormat:
    return load_problem(folder / "problem.yaml")


def problem_format(version: ProblemVersion) -> ProblemFormat:
    """The version's `problem.yaml`, parsed and checked."""
    return _load(version_path(version))


def open_file(version: ProblemVersion, relative: str) -> int:
    """Open a regular file inside the version folder for reading; return the file descriptor.

    `relative` must already be a name we chose to allow (see problems.files). This is the second
    line of defence: no "..", no absolute path, no symlink anywhere in the path (resolving the
    path must not change it), the last part opened with O_NOFOLLOW, and only a regular file (not
    a fifo or a device).
    """
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(p in ("..", ".") for p in parts):
        raise NotInStore(relative)
    root = Path(os.path.realpath(version_path(version)))
    target = root.joinpath(*parts)
    if os.path.realpath(target) != str(target):
        raise NotInStore(relative)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        fd = os.open(target, flags)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR, errno.EISDIR, errno.ENXIO):
            raise NotInStore(relative) from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise NotInStore(relative)
    return fd


def list_files(version: ProblemVersion, folder: str) -> list[str]:
    """Every regular file under `folder` in the version (relative names), skipping symlinks."""
    root = Path(os.path.realpath(version_path(version)))
    start = root / folder
    if start.is_symlink() or not start.is_dir():
        return []
    names = []
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
        for filename in sorted(filenames):
            path = here / filename
            if stat.S_ISREG(os.lstat(path).st_mode):
                names.append(path.relative_to(root).as_posix())
    return names


def read_text(version: ProblemVersion, relative: str, limit: int) -> tuple[str, bool]:
    """Read up to `limit` bytes of a text file in the version; return (text, was_cut_short)."""
    with os.fdopen(open_file(version, relative), "rb") as f:
        data = f.read(limit + 1)
    cut = len(data) > limit
    return data[:limit].decode("utf-8", "replace"), cut


def exists(version: ProblemVersion, relative: str) -> bool:
    """True if `relative` is a regular file inside the version folder."""
    try:
        os.close(open_file(version, relative))
    except NotInStore:
        return False
    return True
