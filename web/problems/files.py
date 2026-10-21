"""Which problem files the public may download, and how to serve them.

The allow-list is built from `problem.yaml` and the version folder: statement PDFs, everything
under attachments/, the sample tests, and grader files listed as downloadable. A request for any
other name is a 404, whatever the URL looks like.
"""

from functools import lru_cache
from pathlib import Path, PurePosixPath

from problems import store
from problems.models import ProblemVersion

_TEXT = {".txt", ".in", ".out", ".ans", ".c", ".h", ".cpp", ".hpp", ".cc", ".py", ".md", ".csv"}
_BINARY = {".pdf": "application/pdf", ".zip": "application/zip", ".png": "image/png"}


def content_type(name: str) -> str:
    """Our own short list; anything unknown downloads as plain bytes."""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in _TEXT:
        return "text/plain; charset=utf-8"
    return _BINARY.get(suffix, "application/octet-stream")


def opens_in_browser(name: str) -> bool:
    """PDF statements open in the browser; everything else is a download."""
    return PurePosixPath(name).suffix.lower() == ".pdf"


@lru_cache(maxsize=256)
def _allowed(folder: Path, version_id: int) -> frozenset[str]:
    version = ProblemVersion.objects.select_related("problem").get(pk=version_id)
    fmt = store.problem_format(version)
    names = {f"statement/{lang}.pdf" for lang in ("th", "en")}
    for sample in fmt.samples:
        names.update({f"tests/{sample}.in", f"tests/{sample}.out"})
    if fmt.grader is not None:
        names.update(fmt.grader.downloadable)
    names.update(store.list_files(version, "attachments"))
    return frozenset(name for name in names if store.exists(version, name))


def downloadable(version: ProblemVersion) -> frozenset[str]:
    """The names in this version that may be served. Versions never change, so it is cached."""
    return _allowed(store.version_path(version), version.pk)


def attachments(version: ProblemVersion) -> list[str]:
    """Downloadable files to list on the problem page (statement PDFs and samples are shown
    elsewhere on the page)."""
    return sorted(
        name for name in downloadable(version) if not name.startswith(("statement/", "tests/"))
    )
