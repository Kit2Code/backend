"""The admin Import page: upload a zip → import report → validation job → publish.

The worker is played by the test through the internal API (as in test_internal_api.py); the
real worker runs the reference solutions in the e2e test.
"""

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core.models import AuditLog
from judging.models import JudgeJob
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient, use_role

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

REPO = Path(__file__).resolve().parents[2]
GROUPMIN = REPO / "fixtures" / "problems" / "groupmin"
PASSWORD = "a-long-test-password-1"
IMPORT = "/admin/grader/import/"


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
    client.force_login(User.objects.create_superuser("boss", password=PASSWORD))
    return client


def zip_of(folder: Path, tmp_path: Path, edit: dict[str, str] | None = None) -> bytes:
    """Zip `folder`; `edit` replaces the text of some files (by relative path)."""
    target = tmp_path / "upload.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                name = path.relative_to(folder).as_posix()
                if edit and name in edit:
                    archive.writestr(name, edit[name])
                else:
                    archive.write(path, name)
    return target.read_bytes()


def upload(client: Client, data: bytes, name: str = "groupmin.zip") -> Any:
    return client.post(IMPORT, {"file": SimpleUploadedFile(name, data, "application/zip")})


def version_url(version: ProblemVersion) -> str:
    return f"/admin/grader/versions/{version.pk}/"


# ---- the worker side ----


def worker() -> Client:
    return Client(headers={"Authorization": "Bearer " + django_settings.WORKER_TOKEN})


def worker_post(path: str, data: dict[str, Any]) -> Any:
    return worker().post(path, json.dumps(data), content_type="application/json")


def claim() -> Any:
    return worker_post("/internal/judge/claim", {"worker_id": "w1", "free_slots": 1})


def solution(
    file: str, expected: float | None, score: float, ok: bool, **extra: Any
) -> dict[str, Any]:
    return {
        "file": file, "lang": "cpp17", "expected_score": expected, "verdict": "AC" if ok else "WA",
        "score": score, "max_score": 100, "ok": ok, "message": "", **extra,
    }  # fmt: skip


def report(job_id: int, solutions: list[dict[str, Any]], worker_id: str = "w1") -> Any:
    body = {"kind": "validate", "solutions": solutions, "warnings": []}
    return worker_post(
        "/internal/judge/report", {"worker_id": worker_id, "job_id": job_id, "report": body}
    )


GOOD = [
    solution("solutions/full.cpp", 100, 100, ok=True),
    solution("solutions/partial.cpp", 30, 30, ok=True),
]
BAD = [
    solution("solutions/full.cpp", 100, 100, ok=True),
    solution("solutions/partial.cpp", 30, 70, ok=False, message="expected 30, got 70"),
]


def imported(staff: Client, tmp_path: Path) -> ProblemVersion:
    response = upload(staff, zip_of(GROUPMIN, tmp_path))
    version = ProblemVersion.objects.get()
    assert response.status_code == 302
    assert response["Location"] == version_url(version)
    return version


def public_status(settings: SettingsWrapper, path: str = "/problems/groupmin") -> int:
    use_role(settings, "public")
    try:
        return EdgeClient().get(path).status_code
    finally:
        use_role(settings, "admin")


# ---- import ----


def test_the_import_page_is_for_staff_only() -> None:
    assert EdgeClient().get(IMPORT).status_code == 302  # to the admin login
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(IMPORT).status_code == 403
    helper.user_permissions.add(Permission.objects.get(codename="change_problem"))
    assert client.get(IMPORT).status_code == 200


def test_an_upload_is_imported_hidden_and_queued_for_validation(
    staff: Client, tmp_path: Path
) -> None:
    version = imported(staff, tmp_path)
    problem = version.problem
    assert problem.slug == "groupmin" and not problem.is_visible
    assert problem.current_version == version
    assert version.status == ProblemVersion.Status.VALIDATING
    job = JudgeJob.objects.get()
    assert (job.kind, job.priority, job.problem_version) == (
        "validate", JudgeJob.Priority.VALIDATION, version,
    )  # fmt: skip
    assert (django_settings.GRADER_PROBLEMS_DIR / "groupmin" / version.dir_name).is_dir()
    page = staff.get(version_url(version)).content.decode()
    assert "groupmin" in page and version.dir_name in page
    assert 'http-equiv="refresh"' in page  # it reloads itself while validating
    row = AuditLog.objects.get(action="problem.import")
    assert row.target == "groupmin" and row.details["version"] == version.dir_name


def test_the_worker_gets_a_validation_job(staff: Client, tmp_path: Path) -> None:
    version = imported(staff, tmp_path)
    response = claim()
    assert response.status_code == 200
    assert response.json() == {
        "job_id": JudgeJob.objects.get().pk,
        "kind": "validate",
        "problem_path": f"groupmin/{version.dir_name}",
        "time_scale": 1.0,
    }


def test_a_bad_zip_shows_the_error_and_stores_nothing(staff: Client) -> None:
    response = upload(staff, b"not a zip at all")
    assert response.status_code == 200
    assert "zip" in response.content.decode().lower()
    assert not Problem.objects.exists()
    store = django_settings.GRADER_PROBLEMS_DIR
    # Only the importer's own (empty) work folder: no problem folder, nothing half-written.
    assert [p.name for p in store.iterdir()] in ([], [".staging"])
    assert not any((store / ".staging").glob("*"))


def test_a_broken_problem_shows_the_importer_message(staff: Client, tmp_path: Path) -> None:
    text = (GROUPMIN / "problem.yaml").read_text(encoding="utf-8")
    broken = text.replace("time_limit_ms: 1000", "time_limit_ms: -5")
    response = upload(staff, zip_of(GROUPMIN, tmp_path, {"problem.yaml": broken}))
    assert response.status_code == 200
    assert "time_limit_ms" in response.content.decode()
    assert not Problem.objects.exists()


def test_a_too_large_upload_is_refused(
    staff: Client, tmp_path: Path, settings: SettingsWrapper
) -> None:
    data = zip_of(GROUPMIN, tmp_path)
    settings.GRADER_MAX_UPLOAD_BYTES = len(data) - 1
    response = upload(staff, data)
    assert response.status_code == 200
    assert not Problem.objects.exists()


def test_the_temporary_upload_is_removed(staff: Client, tmp_path: Path) -> None:
    imported(staff, tmp_path)
    leftovers = [p for p in django_settings.GRADER_UPLOADS_DIR.rglob("*") if p.is_file()]
    assert leftovers == []


# ---- validation and publishing ----


def test_a_passing_validation_makes_it_publishable(
    staff: Client, tmp_path: Path, settings: SettingsWrapper
) -> None:
    version = imported(staff, tmp_path)
    job_id = claim().json()["job_id"]
    assert report(job_id, GOOD).status_code == 204
    version.refresh_from_db()
    assert version.status == ProblemVersion.Status.VALID
    job = JudgeJob.objects.get()
    assert job.status == JudgeJob.Status.DONE
    assert job.result["solutions"][1]["file"] == "solutions/partial.cpp"
    assert public_status(settings) == 404  # valid is not yet published
    response = staff.post(f"{version_url(version)}publish")
    assert response.status_code == 302
    version.problem.refresh_from_db()
    assert version.problem.is_visible
    assert public_status(settings) == 200
    assert AuditLog.objects.filter(action="problem.publish", target="groupmin").exists()


def test_a_failing_solution_keeps_it_hidden_with_a_clear_report(
    staff: Client, tmp_path: Path, settings: SettingsWrapper
) -> None:
    version = imported(staff, tmp_path)
    assert report(claim().json()["job_id"], BAD).status_code == 204
    version.refresh_from_db()
    assert version.status == ProblemVersion.Status.INVALID
    page = staff.get(version_url(version)).content.decode()
    assert "solutions/partial.cpp" in page
    assert "expected 30, got 70" in page
    assert 'http-equiv="refresh"' not in page
    staff.post(f"{version_url(version)}publish")
    version.problem.refresh_from_db()
    assert not version.problem.is_visible
    assert public_status(settings) == 404


def test_validation_that_keeps_failing_marks_the_version_invalid(
    staff: Client, tmp_path: Path
) -> None:
    version = imported(staff, tmp_path)
    for _ in range(3):
        job_id = claim().json()["job_id"]
        fail = {"worker_id": "w1", "job_id": job_id, "error": "isolate broke"}
        assert worker_post("/internal/judge/fail", fail).status_code == 204
    version.refresh_from_db()
    assert version.status == ProblemVersion.Status.INVALID
    assert "isolate broke" in staff.get(version_url(version)).content.decode()


def test_validate_again(staff: Client, tmp_path: Path) -> None:
    version = imported(staff, tmp_path)
    report(claim().json()["job_id"], BAD)
    assert staff.post(f"{version_url(version)}validate").status_code == 302
    version.refresh_from_db()
    assert version.status == ProblemVersion.Status.VALIDATING
    assert JudgeJob.objects.filter(status=JudgeJob.Status.QUEUED, kind="validate").count() == 1


def test_a_new_version_of_a_published_problem_waits_for_publish(
    staff: Client, tmp_path: Path
) -> None:
    first = imported(staff, tmp_path)
    report(claim().json()["job_id"], GOOD)
    staff.post(f"{version_url(first)}publish")
    text = (GROUPMIN / "problem.yaml").read_text(encoding="utf-8")
    changed = text.replace("time_limit_ms: 1000", "time_limit_ms: 2000")
    upload(staff, zip_of(GROUPMIN, tmp_path, {"problem.yaml": changed}))
    second = ProblemVersion.objects.exclude(pk=first.pk).get()
    problem = Problem.objects.get()
    assert problem.current_version == first and problem.is_visible
    report(claim().json()["job_id"], GOOD)
    staff.post(f"{version_url(second)}publish")
    problem.refresh_from_db()
    assert problem.current_version == second


def test_the_same_zip_twice_reuses_the_version(staff: Client, tmp_path: Path) -> None:
    version = imported(staff, tmp_path)
    report(claim().json()["job_id"], GOOD)
    upload(staff, zip_of(GROUPMIN, tmp_path))
    assert ProblemVersion.objects.get() == version
    version.refresh_from_db()
    assert version.status == ProblemVersion.Status.VALID  # nothing to validate again
    assert JudgeJob.objects.count() == 1


# ---- the report endpoint ----


def test_a_report_must_match_the_job(staff: Client, tmp_path: Path) -> None:
    imported(staff, tmp_path)
    job_id = claim().json()["job_id"]
    assert report(job_id, GOOD, worker_id="w2").status_code == 409
    assert report(job_id + 1000, GOOD).status_code == 409
    body = {
        "worker_id": "w1",
        "job_id": job_id,
        "result": {"verdict": "AC", "score": 0, "max_score": 0},
    }
    assert worker_post("/internal/judge/complete", body).status_code == 409  # not a submission
    assert report(job_id, GOOD).status_code == 204
    assert report(job_id, GOOD).status_code == 409  # done already
