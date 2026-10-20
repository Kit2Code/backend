"""Our own problem format: assemble an unpacked upload into a version folder and check it.

The pydantic models in `problem_format.py` check `problem.yaml` on its own. This module checks the
rest: that the files `problem.yaml` names exist, that every test has an input and an output, and
that the folder holds nothing unexpected. Imported CMS tasks are converted to this layout first, so
the same checks apply to them.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from grader_common.importer.errors import ProblemImportError
from grader_common.problem_format import TEST_NAME_RE, GroupMinScoring, Problem
from grader_common.scoring import resolve_subtasks

# What a version folder may contain (meta/ is written by the importer itself).
CONTENT_ENTRIES = (
    "problem.yaml",
    "statement",
    "attachments",
    "tests",
    "checker",
    "graders",
    "solutions",
)
STATEMENT_NAMES = frozenset(f"{lang}.{ext}" for lang in ("th", "en") for ext in ("pdf", "md"))
_MAX_YAML_BYTES = 1 << 20


@dataclass(frozen=True)
class CheckedProblem:
    """A version folder that passed every check."""

    problem: Problem
    test_names: list[str]


def assemble(upload: Path, out: Path) -> list[str]:
    """Move the known parts of an unpacked native upload into the new folder `out`.

    Returns warnings for anything left out. An uploaded `meta/` is always dropped: the importer
    writes its own.
    """
    if not (upload / "problem.yaml").is_file():
        raise ProblemImportError("problem.yaml is missing")
    out.mkdir()
    warnings = []
    for entry in sorted(upload.iterdir(), key=lambda p: p.name):
        if entry.name in CONTENT_ENTRIES:
            os.rename(entry, out / entry.name)
        else:
            warnings.append(f"{entry.name}: not part of the problem format, left out")
    return warnings


def load_problem(path: Path) -> Problem:
    """Read and validate one `problem.yaml`."""
    if path.stat().st_size > _MAX_YAML_BYTES:
        raise ProblemImportError("problem.yaml is larger than 1 MB")
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise ProblemImportError(f"problem.yaml is not valid YAML: {error}") from None
    if not isinstance(data, dict):
        raise ProblemImportError("problem.yaml must be a mapping (key: value pairs)")
    try:
        return Problem.model_validate(data)
    except ValidationError as error:
        raise ProblemImportError("problem.yaml: " + describe_error(error)) from None


def check_version(folder: Path) -> CheckedProblem:
    """Check a whole version folder; raise ProblemImportError with the first problem found."""
    problem = load_problem(folder / "problem.yaml")
    _check_statements(folder)
    test_names = _check_tests(folder)
    for sample in problem.samples:
        if sample not in test_names:
            raise ProblemImportError(f"sample {sample} is not one of the tests")
    if isinstance(problem.scoring, GroupMinScoring):
        try:
            resolve_subtasks(test_names, problem.scoring.subtasks)
        except ValueError as error:
            raise ProblemImportError(str(error)) from None
    if problem.checker.file is not None:
        _require_file(folder, problem.checker.file)
    if problem.grader is not None:
        for files in problem.grader.files.values():
            for name in files:
                _require_file(folder, name)
        for name in problem.grader.downloadable:
            _require_file(folder, name)
    for solution in problem.solutions:
        _require_file(folder, solution.file)
    return CheckedProblem(problem=problem, test_names=test_names)


def _check_statements(folder: Path) -> None:
    statement_dir = folder / "statement"
    names = sorted(p.name for p in statement_dir.iterdir()) if statement_dir.is_dir() else []
    for name in names:
        if name not in STATEMENT_NAMES or not (statement_dir / name).is_file():
            allowed = ", ".join(sorted(STATEMENT_NAMES))
            raise ProblemImportError(f"statement/{name}: statements must be one of {allowed}")
    if not names:
        raise ProblemImportError("statement/ needs at least one of th.pdf, en.pdf, th.md, en.md")


def _check_tests(folder: Path) -> list[str]:
    """Return the test names: every tests/<name>.in must have a matching tests/<name>.out."""
    tests_dir = folder / "tests"
    if not tests_dir.is_dir():
        raise ProblemImportError("tests/ is missing")
    inputs: set[str] = set()
    outputs: set[str] = set()
    for path in tests_dir.iterdir():
        stem, dot, suffix = path.name.rpartition(".")
        if not (
            path.is_file() and dot and suffix in ("in", "out") and TEST_NAME_RE.fullmatch(stem)
        ):
            raise ProblemImportError(f"tests/{path.name}: expected <name>.in or <name>.out")
        (inputs if suffix == "in" else outputs).add(stem)
    for name in sorted(inputs ^ outputs):
        missing = "out" if name in inputs else "in"
        raise ProblemImportError(f"test {name}: tests/{name}.{missing} is missing")
    if not inputs:
        raise ProblemImportError("tests/ has no tests")
    return sorted(inputs)


def _require_file(folder: Path, name: str) -> None:
    if not (folder / name).is_file():
        raise ProblemImportError(f"{name} is listed in problem.yaml but missing")


def describe_error(error: ValidationError, limit: int = 5) -> str:
    """Turn pydantic's error list into one readable line."""
    parts = []
    for item in error.errors()[:limit]:
        location = ".".join(str(part) for part in item["loc"])
        parts.append(f"{location}: {item['msg']}" if location else item["msg"])
    more = len(error.errors()) - limit
    return "; ".join(parts) + (f" (and {more} more)" if more > 0 else "")
