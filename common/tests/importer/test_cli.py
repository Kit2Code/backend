"""The grader-import command line tool."""

import subprocess
import sys
import zipfile
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from grader_common.importer.cli import main

REPO = Path(__file__).resolve().parents[3]
APLUSB = REPO / "fixtures" / "problems" / "aplusb"


def test_import_prints_the_version_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(APLUSB), "--store", str(tmp_path / "store")]) == 0
    out = capsys.readouterr().out.strip()
    assert Path(out).parent == tmp_path / "store" / "aplusb"
    assert Path(out, "problem.yaml").is_file()


def test_importing_again_reports_the_existing_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = str(tmp_path / "store")
    main([str(APLUSB), "--store", store_dir])
    first = capsys.readouterr().out
    assert main([str(APLUSB), "--store", store_dir]) == 0
    second = capsys.readouterr()
    assert second.out == first
    assert "already" in second.err


def test_italian_zip_with_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    task = REPO / "fixtures" / "cms-italian" / "cms-checker"
    archive = tmp_path / "cms-checker.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(task.rglob("*")):
            if path.is_file():
                zf.write(path, f"cms-checker/{path.relative_to(task).as_posix()}")
    assert main([str(archive), "--store", str(tmp_path / "store")]) == 0
    captured = capsys.readouterr()
    assert "/cms-checker/v001-" in captured.out
    assert "warning: primary_language" in captured.err


def test_errors_are_one_line_and_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    communication = REPO / "fixtures" / "cms-italian" / "communication"
    assert main([str(communication), "--store", str(tmp_path / "store")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("grader-import: error: ")
    assert "Communication" in captured.err
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


def test_missing_source_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "nope.zip"), "--store", str(tmp_path / "store")]) == 1
    assert "no such file or folder" in capsys.readouterr().err


def test_store_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(APLUSB)])
    assert exit_info.value.code == 2


def test_console_script_is_installed() -> None:
    (script,) = entry_points(group="console_scripts", name="grader-import")
    assert script.value == "grader_common.importer.cli:main"


def test_runs_as_a_program(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv list, no shell
        [sys.executable, "-m", "grader_common.importer.cli", str(APLUSB), "--store", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "/aplusb/v001-" in result.stdout
