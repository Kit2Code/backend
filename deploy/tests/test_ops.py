"""grader-ops (deploy/ops/): `secrets-init` and `backup-loop`, run from the real image.

Secrets: created once, copied into one folder per service with that service's owner and modes,
and never printed. Backups: a "back up now" request row becomes a dump that pg_restore can read,
the nightly backup runs at BACKUP_TIME, and only the newest BACKUP_KEEP dumps are kept.

The backup tests use a throw-away Postgres with a table shaped like core.models.BackupRun.
Files made inside containers belong to other users, so cleanup also runs in a container.
"""

import shutil
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deploy.tests.conftest import REPO

DOCKER = shutil.which("docker") or "docker"
OPS_IMAGE = "grader-ops:prod-test"
POSTGRES = (
    "postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
)
PREFIX = "grader-ops-test-"
NETWORK = "grader-ops-test"
WEB_UID, POSTGRES_UID = 10001, 70

BACKUP_RUN_TABLE = """
CREATE TABLE core_backuprun (
    id bigserial PRIMARY KEY,
    requested_by_id bigint NULL,
    status varchar(16) NOT NULL,
    file_name varchar(255) NOT NULL DEFAULT '',
    message text NOT NULL DEFAULT '',
    requested_at timestamptz NOT NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL
);
"""


def docker(
    *argv: str, check: bool = True, timeout: float = 600
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv list
        [DOCKER, *argv], capture_output=True, text=True, timeout=timeout, check=check
    )


def as_root(folder: Path, script: str) -> str:
    """Run a shell snippet as root on `folder` (mounted at /x), for files owned by other users."""
    return docker("run", "--rm", "--network", "none", "-v", f"{folder}:/x", "--entrypoint", "sh",
                  OPS_IMAGE, "-c", script).stdout  # fmt: skip


@pytest.fixture(scope="module", autouse=True)
def image() -> None:
    docker("build", "-q", "-f", str(REPO / "deploy/ops/Dockerfile"), "-t", OPS_IMAGE, str(REPO))


@pytest.fixture
def data(tmp_path: Path) -> Iterator[Path]:
    """A stand-in for /DATA/AppData/grader."""
    (tmp_path / "secrets").mkdir()
    (tmp_path / "backups").mkdir()
    yield tmp_path
    as_root(tmp_path, "rm -rf /x/secrets /x/backups")


def secrets_init(data: Path) -> subprocess.CompletedProcess[str]:
    return docker("run", "--rm", "--network", "none", "-v", f"{data / 'secrets'}:/secrets",
                  OPS_IMAGE, "secrets-init", check=False)  # fmt: skip


def listing(data: Path) -> dict[str, str]:
    """`path: uid mode` for everything under secrets/."""
    out = as_root(data, "cd /x/secrets && find . -mindepth 1 -exec stat -c '%n %u %a' {} +")
    return {
        name: f"{uid} {mode}" for name, uid, mode in (line.split() for line in out.splitlines())
    }


def read_secret(data: Path, relative: str) -> str:
    return as_root(data, f"cat /x/secrets/{relative}").strip()


# ---- secrets-init ----


def test_each_service_gets_its_own_folder_with_its_owner_and_modes(data: Path) -> None:
    run = secrets_init(data)
    assert run.returncode == 0, run.stderr
    files = listing(data)
    expected = {
        "./db": f"{POSTGRES_UID} 500", "./db/db_password": f"{POSTGRES_UID} 400",
        "./web": f"{WEB_UID} 500", "./web/db_password": f"{WEB_UID} 400",
        "./web/django_secret_key": f"{WEB_UID} 400",
        # web-admin (uid 10001) reads the worker token too; the worker runs as root.
        "./worker": f"{WEB_UID} 500", "./worker/worker_token": f"{WEB_UID} 400",
        "./backup": "0 500", "./backup/db_password": "0 400",
    }  # fmt: skip
    for name, owner_mode in expected.items():
        assert files.get(name) == owner_mode, (name, files.get(name))
    master = {name: value for name, value in files.items() if name.startswith("./.master")}
    assert master and all(value.startswith("0 ") for value in master.values())
    assert files["./.master"] == "0 700"


def test_the_copies_match_and_the_values_are_strong(data: Path) -> None:
    secrets_init(data)
    password = read_secret(data, "db/db_password")
    assert (
        password == read_secret(data, "web/db_password") == read_secret(data, "backup/db_password")
    )
    assert len(password) >= 32
    assert len(read_secret(data, "web/django_secret_key")) >= 64
    assert len(read_secret(data, "worker/worker_token")) >= 32
    assert password != read_secret(data, "worker/worker_token")


def test_secrets_are_made_once_and_fixed_up_on_every_start(data: Path) -> None:
    secrets_init(data)
    before = {name: read_secret(data, name) for name in ("db/db_password", "worker/worker_token")}
    as_root(data, "chmod 777 /x/secrets/web /x/secrets/web/django_secret_key && "
                  "rm /x/secrets/worker/worker_token")  # fmt: skip
    assert secrets_init(data).returncode == 0
    after = {name: read_secret(data, name) for name in ("db/db_password", "worker/worker_token")}
    assert after == before
    files = listing(data)
    assert files["./web"] == f"{WEB_UID} 500"
    assert files["./web/django_secret_key"] == f"{WEB_UID} 400"


def test_secrets_are_never_printed(data: Path) -> None:
    run = secrets_init(data)
    for name in ("db/db_password", "web/django_secret_key", "worker/worker_token"):
        assert read_secret(data, name) not in run.stdout + run.stderr


# ---- backup-loop ----


@pytest.fixture
def database(data: Path) -> Iterator[str]:
    """A throw-away Postgres using the password secrets-init made; returns its container name."""
    secrets_init(data)
    docker("network", "rm", NETWORK, check=False)
    docker("network", "create", "--internal", NETWORK)
    name = PREFIX + "db"
    docker("rm", "-f", name, check=False)
    docker("run", "-d", "--name", name, "--network", NETWORK, "--network-alias", "db",
           "-e", "POSTGRES_USER=grader", "-e", "POSTGRES_DB=grader",
           "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/db/db_password",
           "-v", f"{data / 'secrets' / 'db'}:/run/secrets/db:ro", POSTGRES)  # fmt: skip
    for _ in range(60):
        if docker("exec", name, "pg_isready", "-U", "grader", check=False).returncode == 0:
            break
        time.sleep(0.5)
    time.sleep(1)  # pg_isready can answer before the init scripts finish and Postgres restarts
    sql(name, BACKUP_RUN_TABLE)
    yield name
    docker("rm", "-f", PREFIX + "loop", name, check=False)
    docker("network", "rm", NETWORK, check=False)


def sql(container: str, statement: str) -> str:
    for _ in range(20):
        run = docker("exec", container, "psql", "-U", "grader", "-d", "grader", "-qAt", "-c",
                     statement, check=False)  # fmt: skip
        if run.returncode == 0:
            return run.stdout.strip()
        time.sleep(0.5)
    raise AssertionError(run.stderr)


def start_loop(data: Path, backups_mount: str = "rw", **env: str) -> None:
    settings = {"BACKUP_POLL_SECONDS": "1", "TZ": "Asia/Bangkok", **env}
    env_args = [arg for key, value in settings.items() for arg in ("-e", f"{key}={value}")]
    docker("run", "-d", "--name", PREFIX + "loop", "--network", NETWORK, *env_args,
           "-v", f"{data / 'backups'}:/backups:{backups_mount}",
           "-v", f"{data / 'secrets' / 'backup'}:/run/secrets/backup:ro",
           OPS_IMAGE, "backup-loop")  # fmt: skip


def wait_for(condition: str, container: str, timeout: float = 60) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = sql(container, condition)
        if value:
            return value
        time.sleep(0.5)
    logs = docker("logs", PREFIX + "loop", check=False)
    raise AssertionError(f"timed out: {condition}\n{logs.stdout}\n{logs.stderr}")


def dumps(data: Path) -> list[str]:
    out = as_root(
        data, 'cd /x/backups && for f in *; do [ -e "$f" ] && stat -c \'%n %u %g %a\' "$f"; done'
    )
    return sorted(out.splitlines())


def test_back_up_now_makes_a_dump_that_pg_restore_reads(data: Path, database: str) -> None:
    row = sql(database, "INSERT INTO core_backuprun (status, requested_at) VALUES "
                        "('requested', now()) RETURNING id")  # fmt: skip
    start_loop(data, BACKUP_TIME="03:30")
    query = (
        "SELECT status || ' ' || file_name FROM core_backuprun"  # noqa: S608 - our own integer id
        f" WHERE id = {int(row)} AND status IN ('done', 'failed')"
    )
    result = wait_for(query, database)
    status, file_name = result.split()
    assert status == "done", result
    assert file_name.startswith("grader-") and file_name.endswith(".dump")
    assert dumps(data) == [f"{file_name} {POSTGRES_UID} {WEB_UID} 640"]
    restored = as_root(data, f"pg_restore --list /x/backups/{file_name} | grep -c core_backuprun")
    assert int(restored) >= 1


def test_the_nightly_backup_runs_once_at_its_time(data: Path, database: str) -> None:
    # A minute ago, in UTC; the loop is told TZ=UTC, so "today after BACKUP_TIME" is now.
    past = (datetime.now(UTC) - timedelta(minutes=1)).strftime("%H:%M")
    start_loop(data, BACKUP_TIME=past, TZ="UTC")
    wait_for("SELECT id FROM core_backuprun WHERE requested_by_id IS NULL AND status = 'done'",
             database)  # fmt: skip
    time.sleep(3)  # three more polls: still only one nightly run today
    count = sql(database, "SELECT count(*) FROM core_backuprun")
    assert count == "1"
    assert len(dumps(data)) == 1


def test_only_the_newest_dumps_are_kept(data: Path, database: str) -> None:
    as_root(data, "cd /x/backups && for d in 20260101 20260102 20260103; do "
                  "echo old > grader-$d-033000.dump; done && echo notes > notes.txt")  # fmt: skip
    sql(database, "INSERT INTO core_backuprun (status, requested_at) VALUES ('requested', now())")
    start_loop(data, BACKUP_TIME="03:30", BACKUP_KEEP="2")
    wait_for("SELECT id FROM core_backuprun WHERE status = 'done'", database)
    time.sleep(1)
    names = [line.split()[0] for line in dumps(data)]
    assert "notes.txt" in names  # never touches other files
    kept = [name for name in names if name.endswith(".dump")]
    assert len(kept) == 2
    assert "grader-20260103-033000.dump" in kept  # the newest old one, plus the new dump
    assert not any(name.endswith(".tmp") for name in names)


def test_a_failed_dump_is_reported_on_the_row(data: Path, database: str) -> None:
    sql(database, "INSERT INTO core_backuprun (status, requested_at) VALUES ('requested', now())")
    start_loop(data, backups_mount="ro", BACKUP_TIME="03:30")  # say, the disk went read-only
    result = wait_for("SELECT message FROM core_backuprun WHERE status = 'failed'", database)
    assert "read-only" in result.lower()
    # The loop carries on: a later request is still picked up.
    sql(database, "INSERT INTO core_backuprun (status, requested_at) VALUES ('requested', now())")
    wait_for("SELECT count(*) FROM core_backuprun WHERE status = 'failed' HAVING count(*) = 2",
             database)  # fmt: skip


def test_the_folders_web_admin_writes_are_given_to_the_web_user(data: Path) -> None:
    # Docker creates missing bind-mount folders as root; web-admin (uid 10001) must write to these.
    for name in ("uploads", "problems"):
        (data / name).mkdir()
    run = docker("run", "--rm", "--network", "none", "-v", f"{data / 'secrets'}:/secrets",
                 "-v", f"{data / 'uploads'}:/data/uploads",
                 "-v", f"{data / 'problems'}:/data/problems",
                 OPS_IMAGE, "secrets-init", check=False)  # fmt: skip
    assert run.returncode == 0, run.stderr
    owners = as_root(data, "stat -c '%n %u %a' /x/uploads /x/problems").split()
    assert owners == ["/x/uploads", str(WEB_UID), "750", "/x/problems", str(WEB_UID), "750"]
    as_root(data, "rm -rf /x/uploads /x/problems")
