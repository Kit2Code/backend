"""`backup-loop`: nightly database backups, plus the ones asked for on the admin Backups page.

Every BACKUP_POLL_SECONDS (30) it looks for `core_backuprun` rows with status "requested" (made by
"Back up now") and, once a day after BACKUP_TIME (local time, from TZ), makes the nightly backup
with a row of its own (no requester). Each backup is `pg_dump -Fc` into /backups as
grader-YYYYmmdd-HHMMSS.dump: written under a temporary name first, then renamed, owned by the
postgres user with the web user's group and mode 0640, so web-admin can list and serve it and
nobody else can read it. Only the newest BACKUP_KEEP dumps are kept.

Settings: BACKUP_TIME ("03:30"), BACKUP_KEEP (14), BACKUP_POLL_SECONDS (30), TZ, and PGHOST (db),
PGUSER and PGDATABASE (grader). The password is read from /run/secrets/backup/db_password and
handed to the Postgres tools in their environment, never on a command line or in a log.
"""

import contextlib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as day_time
from pathlib import Path
from zoneinfo import ZoneInfo

BACKUPS = Path("/backups")
PG_BIN = Path("/usr/local/bin")  # psql and pg_dump of this image (the same version as the server)
PASSWORD_FILE = Path("/run/secrets/backup/db_password")
DUMP_NAME = re.compile(r"grader-\d{8}-\d{6}\.dump")
POSTGRES_UID = 70  # owns the dumps (the postgres user of this image)
WEB_GID = 10001  # web-admin's group: may read them (Backups page)
MAX_MESSAGE = 2000


@dataclass(frozen=True)
class Settings:
    backup_time: day_time
    keep: int
    poll_seconds: float
    zone: ZoneInfo

    @classmethod
    def from_env(cls) -> "Settings":
        hours, minutes = os.environ.get("BACKUP_TIME", "03:30").split(":")
        keep = int(os.environ.get("BACKUP_KEEP", "14"))
        if keep < 1:
            raise ValueError("BACKUP_KEEP must be at least 1")
        return cls(
            backup_time=day_time(int(hours), int(minutes)),
            keep=keep,
            poll_seconds=float(os.environ.get("BACKUP_POLL_SECONDS", "30")),
            zone=ZoneInfo(os.environ.get("TZ", "UTC")),
        )


class BackupError(Exception):
    """A dump failed; the message goes on the BackupRun row."""


def postgres_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PGHOST", "db")
    env.setdefault("PGUSER", "grader")
    env.setdefault("PGDATABASE", "grader")
    env["PGPASSWORD"] = PASSWORD_FILE.read_text(encoding="ascii").strip()
    return env


def psql(script: str, **variables: str) -> str:
    """Run SQL with psql. Values are passed as psql variables (`:'name'` quotes them safely)."""
    argv = [str(PG_BIN / "psql"), "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"]
    for name, value in variables.items():
        argv += ["-v", f"{name}={value}"]
    run = subprocess.run(  # noqa: S603 - fixed argv list; the SQL is fixed text
        argv, input=script, capture_output=True, text=True, env=postgres_env(), timeout=60,
        check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise BackupError(run.stderr.strip()[-MAX_MESSAGE:])
    return run.stdout.strip()


# ---- the request rows ----


def take_request() -> int | None:
    """Mark the oldest requested backup as running and return its id (None if there is none)."""
    row = psql(
        "UPDATE core_backuprun SET status = 'running', started_at = now() WHERE id = ("
        " SELECT id FROM core_backuprun WHERE status = 'requested'"
        " ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id;"
    )
    return int(row) if row else None


def nightly_due(settings: Settings, now: datetime) -> bool:
    """True after today's BACKUP_TIME if no nightly backup was started since then."""
    due_at = datetime.combine(now.date(), settings.backup_time, tzinfo=settings.zone)
    if now < due_at:
        return False
    started = psql(
        "SELECT count(*) FROM core_backuprun WHERE requested_by_id IS NULL"
        " AND requested_at >= :'since'::timestamptz;",
        since=due_at.isoformat(),
    )
    return started == "0"


def start_nightly() -> int:
    return int(
        psql(
            "INSERT INTO core_backuprun (status, file_name, message, requested_at, started_at)"
            " VALUES ('running', '', '', now(), now()) RETURNING id;"
        )
    )


def finish(run_id: int, file_name: str = "", error: str = "") -> None:
    psql(
        "UPDATE core_backuprun SET status = :'status', file_name = :'file', message = :'message',"
        " finished_at = now() WHERE id = :'id'::bigint;",
        status="failed" if error else "done",
        file=file_name,
        message=error[:MAX_MESSAGE],
        id=str(run_id),
    )


# ---- dumps ----


def dump(settings: Settings) -> str:
    """Write a new dump; return its file name. Raises BackupError on failure."""
    name = datetime.now(settings.zone).strftime("grader-%Y%m%d-%H%M%S.dump")
    temporary = BACKUPS / f".{name}.tmp"
    run = subprocess.run(  # noqa: S603 - fixed argv list
        [str(PG_BIN / "pg_dump"), "-Fc", "--no-password", "-f", str(temporary)],
        capture_output=True, text=True, env=postgres_env(), timeout=6 * 3600, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        _remove(temporary)
        raise BackupError(run.stderr.strip()[-MAX_MESSAGE:] or f"pg_dump exited {run.returncode}")
    try:
        os.chown(temporary, POSTGRES_UID, WEB_GID)
        os.chmod(temporary, 0o640)
        os.replace(temporary, BACKUPS / name)
    except OSError as error:
        _remove(temporary)
        raise BackupError(str(error)) from None
    return name


def _remove(path: Path) -> None:
    """Remove a half-written file if we can (on a read-only disk even that fails)."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def prune(keep: int) -> None:
    """Delete all but the newest `keep` dumps (by name, which starts with the date and time)."""
    names = sorted(entry.name for entry in os.scandir(BACKUPS) if DUMP_NAME.fullmatch(entry.name))
    for name in names[:-keep]:
        (BACKUPS / name).unlink(missing_ok=True)
        print(f"backup-loop: removed old backup {name}")


def run_backup(run_id: int, settings: Settings) -> None:
    try:
        name = dump(settings)
    except (BackupError, OSError) as error:
        print(f"backup-loop: backup {run_id} failed: {error}", file=sys.stderr)
        finish(run_id, error=str(error))
        return
    finish(run_id, file_name=name)
    print(f"backup-loop: backup {run_id} written: {name}")
    prune(settings.keep)


# ---- the loop ----


def prepare_folder() -> None:
    """The backups folder: postgres user, web group, 0750 (web-admin lists it read-only)."""
    try:
        os.chown(BACKUPS, POSTGRES_UID, WEB_GID)
        os.chmod(BACKUPS, 0o750)  # noqa: S103 - the web group may list it, others nothing
    except OSError as error:  # a read-only folder shows up as failed backups, with the reason
        print(f"backup-loop: can't prepare {BACKUPS}: {error}", file=sys.stderr)


def wait_for_database(settings: Settings) -> None:
    """Until the database answers and web-admin has created the BackupRun table."""
    while True:
        try:
            psql("SELECT 1 FROM core_backuprun LIMIT 1;")
            return
        except (BackupError, OSError) as error:
            print(f"backup-loop: waiting for the database ({str(error)[:200]})")
            time.sleep(settings.poll_seconds)


def tick(settings: Settings) -> None:
    """One round: every requested backup, then the nightly one if it is due."""
    while (run_id := take_request()) is not None:
        run_backup(run_id, settings)
    if nightly_due(settings, datetime.now(settings.zone)):
        run_backup(start_nightly(), settings)


def main() -> int:
    settings = Settings.from_env()
    prepare_folder()
    wait_for_database(settings)
    print(f"backup-loop: nightly at {settings.backup_time:%H:%M} ({settings.zone}), "
          f"keeping {settings.keep}")  # fmt: skip
    while True:
        try:
            tick(settings)
        except (BackupError, OSError) as error:
            print(f"backup-loop: {str(error)[:MAX_MESSAGE]}", file=sys.stderr)
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
