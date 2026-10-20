"""Importing native-format problems into the store: validation, versions, hashes, clean failure."""

import errno
import json
import os
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from grader_common.importer import store
from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import import_problem

PROBLEM_YAML = """\
format_version: 1
slug: aplusb
title: {en: "A plus B"}
time_limit_ms: 1000
memory_limit_mb: 256
languages: [cpp17, python3]
scoring:
  type: group_min
  subtasks:
    - {name: "1", points: 40, tests: ["01-*"]}
    - {name: "2", points: 60, tests: ["*"]}
samples: [01-a]
checker: {type: cms, file: checker/checker.cpp}
solutions:
  - {file: solutions/main.cpp, lang: cpp17, expected_score: 100}
"""


def write_problem(root: Path, problem_yaml: str = PROBLEM_YAML) -> Path:
    files = {
        "problem.yaml": problem_yaml,
        "statement/en.md": "# A plus B\n",
        "attachments/notes.txt": "hello\n",
        "tests/01-a.in": "1 2\n",
        "tests/01-a.out": "3\n",
        "tests/02-a.in": "5 7\n",
        "tests/02-a.out": "12\n",
        "checker/checker.cpp": "int main() {}\n",
        "solutions/main.cpp": "int main() {}\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def zip_folder(folder: Path, zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder).as_posix())
    return zip_path


@pytest.fixture
def problem_dir(tmp_path: Path) -> Path:
    return write_problem(tmp_path / "upload")


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "store"


def versions(store_dir: Path, slug: str = "aplusb") -> list[str]:
    folder = store_dir / slug
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def assert_nothing_stored(store_dir: Path) -> None:
    assert versions(store_dir) == []
    staging = store_dir / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


# --- a good import -----------------------------------------------------------------------


def test_folder_import_creates_the_first_version(problem_dir: Path, store_dir: Path) -> None:
    result = import_problem(problem_dir, store_dir)
    assert result.created
    assert result.version_dir.parent == store_dir / "aplusb"
    assert result.version_dir.name.startswith("v001-")
    assert len(result.version_dir.name) == len("v001-") + 8
    for name in ["problem.yaml", "statement/en.md", "tests/02-a.out", "checker/checker.cpp"]:
        assert (result.version_dir / name).is_file()
    assert result.problem.slug == "aplusb"
    assert result.test_names == ["01-a", "02-a"]
    report = json.loads((result.version_dir / "meta" / "import-report.json").read_text())
    assert report["format"] == "native"
    assert report["content_sha256"].startswith(result.version_dir.name[5:])
    assert report["warnings"] == []
    assert list((store_dir / ".staging").iterdir()) == []


def test_import_twice_gives_the_same_version(problem_dir: Path, store_dir: Path) -> None:
    first = import_problem(problem_dir, store_dir)
    second = import_problem(problem_dir, store_dir)
    assert not second.created
    assert second.version_dir == first.version_dir
    assert versions(store_dir) == [first.version_dir.name]


def test_zip_and_folder_with_the_same_content_get_the_same_hash(
    problem_dir: Path, store_dir: Path, tmp_path: Path
) -> None:
    from_folder = import_problem(problem_dir, store_dir)
    archive = zip_folder(problem_dir, tmp_path / "aplusb.zip")
    from_zip = import_problem(archive, store_dir)
    assert from_zip.version_dir == from_folder.version_dir


def test_zip_with_a_wrapping_folder_is_accepted(problem_dir: Path, tmp_path: Path) -> None:
    wrapper = tmp_path / "wrap"
    shutil.copytree(problem_dir, wrapper / "aplusb")
    archive = zip_folder(wrapper, tmp_path / "wrapped.zip")
    assert import_problem(archive, tmp_path / "store").created


def test_changed_content_creates_the_next_version(problem_dir: Path, store_dir: Path) -> None:
    first = import_problem(problem_dir, store_dir)
    (problem_dir / "tests" / "02-a.out").write_text("13\n")
    second = import_problem(problem_dir, store_dir)
    assert second.created
    assert second.version_dir.name.startswith("v002-")
    assert second.version_dir.name[5:] != first.version_dir.name[5:]
    assert (first.version_dir / "tests" / "02-a.out").read_text() == "12\n"  # never changed


def test_unknown_entries_are_dropped_with_a_warning(problem_dir: Path, store_dir: Path) -> None:
    (problem_dir / "notes").mkdir()
    (problem_dir / "notes" / "todo.txt").write_text("x")
    (problem_dir / "meta").mkdir()
    (problem_dir / "meta" / "import-report.json").write_text("{}")
    result = import_problem(problem_dir, store_dir)
    assert not (result.version_dir / "notes").exists()
    assert any("notes" in w for w in result.warnings)
    report = json.loads((result.version_dir / "meta" / "import-report.json").read_text())
    assert report["format"] == "native"  # ours, not the uploaded one


# --- the hash and the store --------------------------------------------------------------


def test_hash_covers_names_and_content_but_not_the_report(tmp_path: Path) -> None:
    folder = tmp_path / "v"
    (folder / "meta").mkdir(parents=True)
    (folder / "a.txt").write_text("1")
    base = store.content_hash(folder)
    (folder / "meta" / "import-report.json").write_text('{"anything": 1}')
    assert store.content_hash(folder) == base
    (folder / "a.txt").write_text("2")
    assert store.content_hash(folder) != base
    (folder / "a.txt").write_text("1")
    (folder / "a.txt").rename(folder / "b.txt")
    assert store.content_hash(folder) != base


def test_versions_are_numbered_after_the_highest_existing_one(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    (store_dir / "aplusb" / "v007-deadbeef").mkdir(parents=True)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "a.txt").write_text("1")
    stored = store.store_version(staged, store_dir, "aplusb", store.content_hash(staged))
    assert stored.name.startswith("v008-")


def test_a_concurrent_import_taking_the_name_moves_us_to_the_next_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = tmp_path / "store"
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "a.txt").write_text("1")
    real_rename = os.rename
    calls: list[str] = []

    def rename_losing_the_first_race(src: str | Path, dst: str | Path) -> None:
        calls.append(Path(dst).name)
        if len(calls) == 1:
            raise OSError(errno.ENOTEMPTY, "Directory not empty")
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename_losing_the_first_race)
    stored = store.store_version(staged, store_dir, "aplusb", store.content_hash(staged))
    assert [name[:4] for name in calls] == ["v001", "v002"]
    assert stored.name.startswith("v002-")


# --- refused uploads leave nothing behind ------------------------------------------------


def edit_yaml(problem_dir: Path, old: str, new: str) -> None:
    path = problem_dir / "problem.yaml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: (d / "problem.yaml").unlink(), "problem.yaml"),
        (lambda d: (d / "problem.yaml").write_text("a: [unclosed"), "YAML"),
        (lambda d: (d / "problem.yaml").write_text("- just\n- a list\n"), "mapping"),
        (
            lambda d: edit_yaml(d, "memory_limit_mb: 256", "memory_limit_mb: 4096"),
            "memory_limit_mb",
        ),
        (lambda d: shutil.rmtree(d / "statement"), "statement"),
        (lambda d: (d / "statement" / "fr.md").write_text("x"), "fr.md"),
        (lambda d: (d / "tests" / "02-a.out").unlink(), "02-a"),
        (lambda d: (d / "tests" / "03.ans").write_text("x"), "03.ans"),
        (lambda d: shutil.rmtree(d / "tests"), "tests"),
        (lambda d: edit_yaml(d, "samples: [01-a]", 'samples: ["99"]'), "99"),
        (lambda d: edit_yaml(d, 'tests: ["01-*"]', 'tests: ["05-*"]'), "05-*"),
        (lambda d: (d / "checker" / "checker.cpp").unlink(), "checker/checker.cpp"),
        (lambda d: (d / "solutions" / "main.cpp").unlink(), "solutions/main.cpp"),
    ],
    ids=[
        "no-problem-yaml",
        "bad-yaml",
        "yaml-not-mapping",
        "invalid-field",
        "no-statement",
        "unknown-statement",
        "missing-output",
        "stray-test-file",
        "no-tests",
        "sample-not-a-test",
        "pattern-matches-nothing",
        "missing-checker",
        "missing-solution",
    ],
)
def test_bad_problems_are_refused(
    problem_dir: Path, store_dir: Path, change: Callable[[Path], object], message: str
) -> None:
    change(problem_dir)
    with pytest.raises(ProblemImportError, match=message.replace("*", r"\*")):
        import_problem(problem_dir, store_dir)
    assert_nothing_stored(store_dir)


def test_missing_grader_file_is_refused(problem_dir: Path, store_dir: Path) -> None:
    edit_yaml(
        problem_dir,
        "solutions:",
        'grader: {submission_file: "aplusb.%l", files: {cpp17: [graders/grader.cpp]}}\nsolutions:',
    )
    with pytest.raises(ProblemImportError, match="graders/grader.cpp"):
        import_problem(problem_dir, store_dir)
    assert_nothing_stored(store_dir)


def test_unsafe_zip_is_refused_before_anything_is_stored(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "x")
    store_dir = tmp_path / "store"
    with pytest.raises(ProblemImportError):
        import_problem(archive, store_dir)
    assert_nothing_stored(store_dir)
    assert not (tmp_path / "escape.txt").exists()
