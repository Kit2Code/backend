"""The admin Backups page: list the backup files, download one, and ask for a backup now.

The backups themselves are made by the backup service (pg_dump), which picks up the request rows;
web-admin only reads the backups folder.
"""

import os
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core.models import AuditLog, BackupRun
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PAGE = "/admin/grader/backups/"
PASSWORD = "a-long-test-password-1"


@pytest.fixture(autouse=True)
def backups(settings: SettingsWrapper, tmp_path: Path) -> Path:
    folder = tmp_path / "backups"
    folder.mkdir()
    settings.GRADER_BACKUPS_DIR = folder
    return folder


@pytest.fixture
def boss() -> User:
    return User.objects.create_superuser("boss", password=PASSWORD)


@pytest.fixture
def staff(boss: User) -> Client:
    client = EdgeClient()
    client.force_login(boss)
    return client


def put(
    folder: Path, name: str, data: bytes = b"PGDMP fake dump", mtime: int = 1_700_000_000
) -> Path:
    path = folder / name
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


# ---- access ----


def test_the_page_is_for_staff_who_may_see_backups(backups: Path) -> None:
    put(backups, "grader-2026-10-20.dump")
    assert EdgeClient().get(PAGE).status_code == 302
    assert EdgeClient().get(f"{PAGE}grader-2026-10-20.dump").status_code == 302
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(PAGE).status_code == 403
    assert client.get(f"{PAGE}grader-2026-10-20.dump").status_code == 403
    helper.user_permissions.add(Permission.objects.get(codename="view_backuprun"))
    assert client.get(PAGE).status_code == 200
    assert client.post(PAGE, {"action": "backup_now"}).status_code == 403  # needs "add"
    assert not BackupRun.objects.exists()


# ---- listing and downloading ----


def test_backup_files_are_listed_newest_first(staff: Client, backups: Path) -> None:
    put(backups, "grader-2026-10-19.dump", b"x" * 2048, mtime=1_700_000_000)
    put(backups, "grader-2026-10-20.dump", b"x" * 10, mtime=1_700_086_400)
    put(backups, "notes.txt")  # not a backup
    (backups / "half-written.dump.tmp").write_bytes(b"x")
    html = staff.get(PAGE).content.decode()
    assert html.index("grader-2026-10-20.dump") < html.index("grader-2026-10-19.dump")
    assert "notes.txt" not in html and "half-written" not in html
    assert 'data-backup="grader-2026-10-19.dump" data-size="2048"' in html


def test_a_backup_can_be_downloaded_and_it_is_audited(staff: Client, backups: Path) -> None:
    put(backups, "grader-2026-10-20.dump", b"PGDMP the real bytes")
    response = staff.get(f"{PAGE}grader-2026-10-20.dump")
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"PGDMP the real bytes"  # type: ignore[attr-defined]
    assert "attachment" in response["Content-Disposition"]
    assert AuditLog.objects.get(action="backup.download").target == "grader-2026-10-20.dump"


@pytest.mark.parametrize(
    "name",
    ["notes.txt", "missing.dump", "..%2Fsecret.dump", ".hidden.dump", "a b.dump", "link.dump"],
)
def test_only_backup_files_in_the_folder_can_be_downloaded(
    staff: Client, backups: Path, tmp_path: Path, name: str
) -> None:
    put(backups, "notes.txt")
    put(tmp_path, "secret.dump", b"outside")
    (backups / "link.dump").symlink_to(tmp_path / "secret.dump")
    put(backups, ".hidden.dump")
    response = staff.get(f"{PAGE}{name}")
    assert response.status_code == 404


def test_a_missing_backups_folder_is_shown_not_a_crash(
    staff: Client, settings: SettingsWrapper, tmp_path: Path
) -> None:
    settings.GRADER_BACKUPS_DIR = tmp_path / "nowhere"
    assert staff.get(PAGE).status_code == 200


# ---- back up now ----


def test_back_up_now_creates_one_request_row(staff: Client, boss: User) -> None:
    assert staff.post(PAGE, {"action": "backup_now"}).status_code == 302
    assert staff.post(PAGE, {"action": "backup_now"}).status_code == 302  # one is waiting already
    run = BackupRun.objects.get()
    assert (run.status, run.requested_by) == (BackupRun.Status.REQUESTED, boss)
    assert AuditLog.objects.filter(action="backup.request").count() == 1
    html = staff.get(PAGE).content.decode()
    assert f'data-run="{run.pk}" data-status="requested"' in html


def test_after_a_backup_finishes_a_new_one_can_be_asked_for(staff: Client) -> None:
    staff.post(PAGE, {"action": "backup_now"})
    BackupRun.objects.update(status=BackupRun.Status.DONE)
    staff.post(PAGE, {"action": "backup_now"})
    assert BackupRun.objects.count() == 2


def test_only_superusers_may_download_a_backup(backups: Path) -> None:
    # A dump holds every session and password hash: downloading it is as good as being superuser.
    put(backups, "grader-2026-10-20.dump")
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    helper.user_permissions.add(Permission.objects.get(codename="view_backuprun"))
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(PAGE).status_code == 200
    assert client.get(f"{PAGE}grader-2026-10-20.dump").status_code == 403
    assert "grader-2026-10-20.dump</a>" not in client.get(PAGE).content.decode()


def test_a_fifo_named_like_a_backup_is_404_without_hanging(staff: Client, backups: Path) -> None:
    os.mkfifo(backups / "pipe.dump")
    assert staff.get(f"{PAGE}pipe.dump").status_code == 404
