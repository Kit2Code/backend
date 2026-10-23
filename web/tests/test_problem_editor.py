"""Creating and editing a problem in the browser. The form becomes a native problem folder, which
goes through the same importer, versions and validation queue as an uploaded zip."""

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core.models import AuditLog
from judging.models import JudgeJob
from problems import store
from problems.models import Problem, ProblemGroup, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

REPO = Path(__file__).resolve().parents[2]
APLUSB = REPO / "fixtures" / "problems" / "aplusb"
NEW = "/admin/grader/problems/new"


@pytest.fixture(autouse=True)
def folders(settings: SettingsWrapper, tmp_path: Path) -> Path:
    settings.GRADER_PROBLEMS_DIR = tmp_path / "problems"
    settings.GRADER_UPLOADS_DIR = tmp_path / "uploads"
    settings.GRADER_PROBLEMS_DIR.mkdir()
    settings.GRADER_UPLOADS_DIR.mkdir()
    return tmp_path


@pytest.fixture
def staff() -> Client:
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True, is_superuser=True))
    return client


def upload(path: Path, name: str | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name or path.name, path.read_bytes())


def aplusb_tests() -> list[SimpleUploadedFile]:
    return [upload(path) for path in sorted((APLUSB / "tests").iterdir())]


def form(**changes: Any) -> dict[str, Any]:
    """A complete, valid new-problem form for a copy of aplusb."""
    data: dict[str, Any] = {
        "slug": "addtwo",
        "title_th": "บวกสองจำนวน",
        "title_en": "Add two",
        "tags": "math, easy",
        "time_limit_ms": "1000",
        "memory_limit_mb": "256",
        "languages": ["c11", "cpp17", "cpp20", "python3"],
        "statement_th": (APLUSB / "statement" / "th.md").read_text(encoding="utf-8"),
        "statement_en": "",
        "tests": aplusb_tests(),
        "samples": "01",
        "scoring": "sum",
        "total": "100",
        "subtasks": "",
        "checker": "diff",
        "solution1": upload(APLUSB / "solutions" / "main.cpp"),
        "solution1_lang": "cpp17",
        "solution1_score": "100",
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def saved_yaml(problem: Problem) -> dict[str, Any]:
    assert problem.current_version is not None
    path = store.version_path(problem.current_version) / "problem.yaml"
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def errors_of(response: Any) -> str:
    assert response.status_code == 200, response.status_code
    html: str = response.content.decode()
    return html


# ---- a new problem ----


def test_new_problem_goes_through_the_importer_and_validation(staff: Client) -> None:
    group = ProblemGroup.objects.create(slug="basics", name_th="พื้นฐาน")
    response = staff.post(NEW, form(group=group.pk))
    assert response.status_code == 302
    problem = Problem.objects.get(slug="addtwo")
    version = problem.current_version
    assert version is not None
    assert response["Location"] == f"/admin/grader/versions/{version.pk}/"
    assert version.status == ProblemVersion.Status.VALIDATING
    assert JudgeJob.objects.filter(kind=JudgeJob.Kind.VALIDATE, problem_version=version).exists()
    assert not problem.is_visible
    assert problem.group == group
    assert problem.title_th == "บวกสองจำนวน" and problem.max_score == 100
    meta = saved_yaml(problem)
    assert meta["scoring"] == {"type": "sum", "total": 100.0}
    assert meta["samples"] == ["01"]
    assert meta["tags"] == ["math", "easy"]
    assert meta["solutions"] == [
        {"file": "solutions/solution1.cpp", "lang": "cpp17", "expected_score": 100.0}
    ]
    folder = store.version_path(version)
    assert sorted(p.name for p in (folder / "tests").iterdir()) == [
        "01.in", "01.out", "02.in", "02.out", "03.in", "03.out",
    ]  # fmt: skip
    assert (folder / "statement" / "th.md").read_text(encoding="utf-8").startswith("#")
    assert AuditLog.objects.filter(action="problem.import").exists()


def test_tests_can_come_as_one_zip(staff: Client) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted((APLUSB / "tests").iterdir()):
            archive.write(path, f"tests/{path.name}")
    archive_upload = SimpleUploadedFile("tests.zip", buffer.getvalue(), "application/zip")
    response = staff.post(NEW, form(tests=None, tests_zip=archive_upload))
    assert response.status_code == 302, response.content.decode()[:2000]
    folder = store.version_path(Problem.objects.get(slug="addtwo").current_version)  # type: ignore[arg-type]
    assert (folder / "tests" / "03.out").is_file()


def test_subtask_scoring(staff: Client) -> None:
    response = staff.post(NEW, form(scoring="group_min", subtasks="30 01\n70 02 03", total=""))
    assert response.status_code == 302, response.content.decode()[:2000]
    meta = saved_yaml(Problem.objects.get(slug="addtwo"))
    assert meta["scoring"] == {
        "type": "group_min",
        "subtasks": [
            {"name": "1", "points": 30.0, "tests": ["01"]},
            {"name": "2", "points": 70.0, "tests": ["02", "03"]},
        ],
    }


def test_a_cms_checker_is_uploaded(staff: Client, tmp_path: Path) -> None:
    checker = SimpleUploadedFile("anything.cpp", b"int main(){return 0;}\n")
    response = staff.post(NEW, form(checker="cms", checker_file=checker))
    assert response.status_code == 302
    problem = Problem.objects.get(slug="addtwo")
    assert saved_yaml(problem)["checker"] == {"type": "cms", "file": "checker/checker.cpp"}


def test_a_pdf_statement(staff: Client) -> None:
    pdf = SimpleUploadedFile("statement.pdf", b"%PDF-1.4\n%fake\n", "application/pdf")
    response = staff.post(NEW, form(statement_th="", statement_pdf_th=pdf))
    assert response.status_code == 302
    folder = store.version_path(Problem.objects.get(slug="addtwo").current_version)  # type: ignore[arg-type]
    assert (folder / "statement" / "th.pdf").is_file()


# ---- bad input shows errors and stores nothing ----


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"slug": "Bad Slug"}, "slug"),
        ({"tests": None}, "ทดสอบ"),
        ({"tests": [upload(APLUSB / "tests" / "01.in")]}, "01.out"),  # the importer's own check
        ({"samples": "99"}, "99"),
        ({"scoring": "group_min", "subtasks": "thirty 01"}, "subtask"),
        ({"scoring": "group_min", "subtasks": "100 nomatch-*"}, "nomatch"),
        ({"languages": ["cobol"]}, "cobol"),
        ({"checker": "cms"}, "checker"),
        ({"statement_th": "", "statement_en": ""}, "statement"),
        ({"time_limit_ms": "0"}, "time_limit_ms"),
        ({"solution1_lang": "python3"}, "solution"),  # a .cpp file can't be Python
    ],
)
def test_bad_input_shows_errors(
    staff: Client, folders: Path, changes: dict[str, Any], message: str
) -> None:
    html = errors_of(staff.post(NEW, form(**changes)))
    assert 'class="errorlist' in html or 'class="editor-error"' in html
    assert message.lower() in html.lower()
    assert not Problem.objects.filter(slug="addtwo").exists()
    assert not (folders / "problems" / "addtwo").exists()  # nothing stored


def test_a_slug_that_exists_points_to_edit(staff: Client) -> None:
    staff.post(NEW, form())
    html = errors_of(staff.post(NEW, form()))
    problem = Problem.objects.get(slug="addtwo")
    assert f"/admin/grader/problems/{problem.pk}/edit" in html
    assert ProblemVersion.objects.filter(problem=problem).count() == 1


# ---- editing ----


def imported_aplusb() -> Problem:
    call_command("import_problem", str(APLUSB), verbosity=0)
    return Problem.objects.get(slug="aplusb")


def edit_form(problem: Problem, client: Client) -> dict[str, Any]:
    """The edit form's starting values, as the page shows them."""
    response = client.get(f"/admin/grader/problems/{problem.pk}/edit")
    assert response.status_code == 200
    initial: dict[str, Any] = dict(response.context["form"].initial)
    return {key: value for key, value in initial.items() if value not in (None, "")}


def test_edit_starts_from_the_current_version(staff: Client) -> None:
    problem = imported_aplusb()
    initial = edit_form(problem, staff)
    assert initial["title_th"] == "เอบวกบี"
    assert initial["time_limit_ms"] == 1000
    assert initial["samples"] == "01"
    assert initial["statement_th"].startswith("#")
    assert initial["scoring"] == "sum" and initial["total"] == 100


def test_edit_without_new_files_keeps_them_and_makes_a_new_version(staff: Client) -> None:
    problem = imported_aplusb()
    first = problem.current_version
    data = edit_form(problem, staff)
    data["title_en"] = "A plus B (edited)"
    data["time_limit_ms"] = "2000"
    response = staff.post(f"/admin/grader/problems/{problem.pk}/edit", data)
    assert response.status_code == 302, response.content.decode()[:2000]
    version = ProblemVersion.objects.filter(problem=problem).exclude(pk=first.pk).get()  # type: ignore[union-attr]
    assert version.dir_name.startswith("v002-")
    folder = store.version_path(version)
    assert sorted(p.name for p in (folder / "tests").iterdir())[:2] == ["01.in", "01.out"]
    assert sorted(p.name for p in (folder / "solutions").iterdir()) == [
        "main.cpp", "main.py", "overflow.cpp",
    ]  # fmt: skip
    meta = yaml.safe_load((folder / "problem.yaml").read_text(encoding="utf-8"))
    assert meta["time_limit_ms"] == 2000
    assert meta["title"]["en"] == "A plus B (edited)"
    assert len(meta["solutions"]) == 3  # kept as they were


def test_edit_with_new_tests_replaces_them(staff: Client) -> None:
    problem = imported_aplusb()
    data = edit_form(problem, staff)
    data["tests"] = [
        SimpleUploadedFile("a.in", b"1 1\n"),
        SimpleUploadedFile("a.out", b"2\n"),
    ]
    data["samples"] = "a"
    response = staff.post(f"/admin/grader/problems/{problem.pk}/edit", data)
    assert response.status_code == 302, response.content.decode()[:2000]
    problem.refresh_from_db()
    folder = store.version_path(problem.current_version)  # type: ignore[arg-type]
    assert sorted(p.name for p in (folder / "tests").iterdir()) == ["a.in", "a.out"]


def test_editing_a_public_problem_keeps_the_public_version_until_publish(staff: Client) -> None:
    problem = imported_aplusb()
    Problem.objects.filter(pk=problem.pk).update(is_visible=True)
    shown = problem.current_version
    data = edit_form(problem, staff)
    data["title_en"] = "Changed"
    staff.post(f"/admin/grader/problems/{problem.pk}/edit", data)
    problem.refresh_from_db()
    assert problem.current_version == shown  # the public keeps the old one until it's validated


# ---- preview and permissions ----


def test_markdown_preview_is_sanitized(staff: Client) -> None:
    response = staff.post(
        "/admin/grader/problems/preview",
        {"statement_th": "# Hi\n\n<script>alert(1)</script> *a*"},
        headers={"HX-Request": "true"},
    )
    html = response.content.decode()
    assert "<h1>Hi</h1>" in html and "<em>a</em>" in html
    assert "<script>" not in html


def test_editor_needs_the_change_problem_permission() -> None:
    viewer = User.objects.create_user(username="viewer", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_problem"))
    client = EdgeClient()
    client.force_login(viewer)
    assert client.get(NEW).status_code == 403
    assert client.post(NEW, form()).status_code == 403
    assert client.post("/admin/grader/problems/preview", {"statement_th": "x"}).status_code == 403


def test_problems_page_and_dashboard_link_the_editor(staff: Client) -> None:
    assert f'href="{NEW}"' in staff.get("/admin/grader/problems/").content.decode()
    assert f'href="{NEW}"' in staff.get("/admin/").content.decode()


def test_many_test_files_at_once(staff: Client) -> None:
    files = []
    for number in range(150):  # 300 files: more than Django's default of 100 per request
        files.append(SimpleUploadedFile(f"{number:03d}.in", b"1 2\n"))
        files.append(SimpleUploadedFile(f"{number:03d}.out", b"3\n"))
    response = staff.post(NEW, form(tests=files, samples="000"))
    assert response.status_code == 302, response.status_code
    folder = store.version_path(Problem.objects.get(slug="addtwo").current_version)  # type: ignore[arg-type]
    assert len(list((folder / "tests").iterdir())) == 300
