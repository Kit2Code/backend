"""Every fixtures/cms-italian task converts to its hand-written golden problem.yaml."""

from pathlib import Path

import pytest
import yaml

from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import import_problem

REPO = Path(__file__).resolve().parents[3]
ITALIAN = REPO / "fixtures" / "cms-italian"
GOLDEN = Path(__file__).parent / "golden"
SUPPORTED = sorted(p.name for p in ITALIAN.iterdir() if p.is_dir() and p.name != "communication")

# What else each import must produce: (test count, files that must exist, warning fragments).
EXTRA: dict[str, tuple[int, list[str], list[str]]] = {
    "aplusb": (3, ["statement/en.pdf"], ["public_testcases 000"]),
    "groupmin": (4, ["statement/th.pdf", "statement/en.pdf", "meta/original/GEN"], []),
    "cms-checker": (3, ["statement/th.pdf", "checker/checker.cpp"], ["primary_language 'it'"]),
    "grader-stub": (
        3,
        [
            "statement/en.pdf",
            "graders/grader.c",
            "graders/grader.cpp",
            "graders/adder.h",
            "attachments/adder.h",
        ],
        [],
    ),
    "big-output": (2, ["statement/en.pdf"], []),
}


def test_every_italian_fixture_has_a_golden_file() -> None:
    assert sorted(SUPPORTED) == sorted(EXTRA)
    assert sorted(p.name.removesuffix(".problem.yaml") for p in GOLDEN.glob("*.yaml")) == SUPPORTED


@pytest.mark.parametrize("name", SUPPORTED)
def test_conversion_matches_the_golden_file(name: str, tmp_path: Path) -> None:
    result = import_problem(ITALIAN / name, tmp_path / "store")
    produced = yaml.safe_load((result.version_dir / "problem.yaml").read_text(encoding="utf-8"))
    expected = yaml.safe_load((GOLDEN / f"{name}.problem.yaml").read_text(encoding="utf-8"))
    assert produced == expected

    n_tests, files, warning_parts = EXTRA[name]
    assert len(result.test_names) == n_tests
    for file in files:
        assert (result.version_dir / file).is_file(), file
    assert (result.version_dir / "meta" / "original" / "task.yaml").is_file()
    assert len(result.warnings) == len(warning_parts), result.warnings
    for part in warning_parts:
        assert any(part in warning for warning in result.warnings), (part, result.warnings)


def test_the_communication_task_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    with pytest.raises(ProblemImportError, match="Communication tasks are not supported"):
        import_problem(ITALIAN / "communication", store_dir)
    assert [p.name for p in store_dir.iterdir()] == [".staging"]
    assert list((store_dir / ".staging").iterdir()) == []
