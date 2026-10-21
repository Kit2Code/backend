"""The problem file view: only an allow-list of files is ever served from the store.

Allowed: statement PDFs, attachments/, the sample tests, and grader files marked downloadable.
Never: other tests, solutions, checkers, graders, problem.yaml, meta/ — whatever the URL says.
"""

import os
import shutil
from pathlib import Path

import pytest
from django.core.management import call_command
from pytest_django.fixtures import Settings as SettingsWrapper

from problems.models import Problem
from problems.store import version_path
from tests.conftest import EdgeClient

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


@pytest.fixture
def store(tmp_path: Path, settings: SettingsWrapper) -> Path:
    folder = tmp_path / "store"
    folder.mkdir()
    settings.GRADER_PROBLEMS_DIR = folder
    return folder


def add(source: Path, *flags: str) -> Problem:
    call_command("import_problem", str(source), *flags, verbosity=0)
    return Problem.objects.order_by("-updated_at").first()  # type: ignore[return-value]


@pytest.fixture
def aplusb(store: Path, tmp_path: Path) -> Problem:
    """aplusb plus two attachments, published."""
    source = tmp_path / "aplusb"
    shutil.copytree(FIXTURES / "problems" / "aplusb", source)
    (source / "attachments" / "sub").mkdir(parents=True)
    (source / "attachments" / "template.cpp").write_text("int main() {}\n")
    (source / "attachments" / "sub" / "notes.txt").write_text("notes\n")
    return add(source, "--visible")


def body(response: object) -> bytes:
    return b"".join(response.streaming_content)  # type: ignore[attr-defined]


# ---- allowed ----


def test_statement_pdf(store: Path) -> None:
    problem = add(FIXTURES / "cms-italian" / "groupmin", "--visible")
    response = EdgeClient().get(f"/problems/{problem.slug}/files/statement/th.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    assert response["X-Content-Type-Options"] == "nosniff"
    expected = (FIXTURES / "cms-italian" / "groupmin" / "statement" / "thai.pdf").read_bytes()
    assert body(response) == expected


def test_sample_test_files(aplusb: Problem) -> None:
    for name in ("tests/01.in", "tests/01.out"):
        response = EdgeClient().get(f"/problems/aplusb/files/{name}")
        assert response.status_code == 200, name
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response["Content-Disposition"].startswith("attachment")
        assert body(response) == (FIXTURES / "problems" / "aplusb" / name).read_bytes()


def test_attachments_including_subfolders(aplusb: Problem) -> None:
    response = EdgeClient().get("/problems/aplusb/files/attachments/template.cpp")
    assert response.status_code == 200
    assert body(response) == b"int main() {}\n"
    assert 'filename="template.cpp"' in response["Content-Disposition"]
    assert EdgeClient().get("/problems/aplusb/files/attachments/sub/notes.txt").status_code == 200


def test_downloadable_grader_file_only(store: Path) -> None:
    add(FIXTURES / "problems" / "grader-stub", "--visible")
    assert EdgeClient().get("/problems/grader-stub/files/graders/adder.h").status_code == 200
    for name in ("graders/grader.cpp", "graders/grader.c"):
        assert EdgeClient().get(f"/problems/grader-stub/files/{name}").status_code == 404


def test_problem_page_links_to_downloads(aplusb: Problem) -> None:
    html = EdgeClient().get("/problems/aplusb").content.decode()
    assert 'href="/problems/aplusb/files/attachments/template.cpp"' in html
    assert 'href="/problems/aplusb/files/tests/01.in"' in html


# ---- refused ----


@pytest.mark.parametrize(
    "name",
    [
        "tests/02.in",
        "tests/02.out",
        "tests/03.in",
        "tests/",
        "tests",
        "solutions/main.cpp",
        "solutions/main.py",
        "solutions",
        "problem.yaml",
        "meta/import-report.json",
        "statement/th.md",
        "statement/en.md",
        "statement/fr.pdf",
        "attachments",
        "attachments/",
        "attachments/sub",
        "attachments/missing.txt",
        "../aplusb/problem.yaml",
        "statement/../problem.yaml",
        "attachments/../solutions/main.cpp",
        "attachments/../../../../../../etc/passwd",
        "%2e%2e/problem.yaml",
        "%2e%2e%2fproblem.yaml",
        "attachments/%2e%2e/solutions/main.cpp",
        "attachments%2f..%2fsolutions%2fmain.cpp",
        "..%2fproblem.yaml",
        "/etc/passwd",
        "//etc/passwd",
        "attachments/template.cpp%00.pdf",
        "attachments//template.cpp",
        "./attachments/template.cpp",
        "attachments/./template.cpp",
        "ATTACHMENTS/template.cpp",
        "attachments\\template.cpp",
        "tests/01.in/",
    ],
)
def test_everything_else_is_404(aplusb: Problem, name: str) -> None:
    assert EdgeClient().get(f"/problems/aplusb/files/{name}").status_code == 404


def test_checker_is_never_served(store: Path) -> None:
    add(FIXTURES / "problems" / "cms-checker", "--visible")
    assert EdgeClient().get("/problems/cms-checker/files/checker/checker.cpp").status_code == 404


def test_hidden_problem_files_are_404(store: Path) -> None:
    problem = add(FIXTURES / "cms-italian" / "groupmin")
    response = EdgeClient().get(f"/problems/{problem.slug}/files/statement/th.pdf")
    assert response.status_code == 404


def test_a_symlink_in_the_store_is_not_followed(aplusb: Problem) -> None:
    # The importer refuses symlinks; this checks the view's own guard if one got in anyway.
    assert aplusb.current_version is not None
    folder = version_path(aplusb.current_version)
    link = folder / "attachments" / "link.txt"
    os.symlink(folder / "solutions" / "main.cpp", link)
    assert EdgeClient().get("/problems/aplusb/files/attachments/link.txt").status_code == 404


def test_a_symlinked_attachments_folder_is_not_followed(store: Path, tmp_path: Path) -> None:
    source = tmp_path / "aplusb"
    shutil.copytree(FIXTURES / "problems" / "aplusb", source)
    problem = add(source, "--visible")
    assert problem.current_version is not None
    folder = version_path(problem.current_version)
    os.symlink(folder / "solutions", folder / "attachments")
    assert EdgeClient().get("/problems/aplusb/files/attachments/main.cpp").status_code == 404


def test_a_fifo_is_not_opened(aplusb: Problem) -> None:
    assert aplusb.current_version is not None
    os.mkfifo(version_path(aplusb.current_version) / "attachments" / "pipe.txt")
    assert EdgeClient().get("/problems/aplusb/files/attachments/pipe.txt").status_code == 404


def test_post_is_not_allowed(aplusb: Problem) -> None:
    response = EdgeClient().post("/problems/aplusb/files/tests/01.in")
    assert response.status_code == 405
