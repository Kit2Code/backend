"""Every synthetic fixture problem is a valid problem, and imports cleanly."""

from pathlib import Path

import pytest

from grader_common.importer.native import check_version
from grader_common.importer.pipeline import import_problem

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NATIVE = sorted(p for p in (FIXTURES / "problems").iterdir() if p.is_dir())
EXPECTED_NATIVE = {
    "aplusb",
    "big-output",
    "cms-checker",
    "grader-stub",
    "groupmin",
    "python-only",
    "testlib-checker",
}


def test_every_planned_fixture_exists() -> None:
    assert {p.name for p in NATIVE} == EXPECTED_NATIVE


@pytest.mark.parametrize("folder", NATIVE, ids=[p.name for p in NATIVE])
def test_fixture_is_a_valid_version_folder(folder: Path) -> None:
    checked = check_version(folder)
    assert checked.problem.slug == folder.name
    assert checked.problem.solutions, "every fixture needs a reference solution"
    assert any(s.expected_score == checked.problem.max_score() for s in checked.problem.solutions)


@pytest.mark.parametrize("folder", NATIVE, ids=[p.name for p in NATIVE])
def test_fixture_imports_without_warnings(folder: Path, tmp_path: Path) -> None:
    result = import_problem(folder, tmp_path / "store")
    assert result.created
    assert result.warnings == []
