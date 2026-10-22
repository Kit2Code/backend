"""`secrets-init`: make the grader's secrets once, and give each service its own copy.

Runs at every start of the app, before the other services, with the secrets folder mounted at
/secrets (on the NAS: /DATA/AppData/grader/secrets). The real values live in /secrets/.master/
(root only) and are made the first time; after that they never change. Each service then gets a
folder with copies of only the secrets it needs, owned by the user it runs as, readable by nobody
else. Copies and modes are written again on every start, so a mistake made by hand fixes itself.

The web roles get the password of the plain database role (db_app_password, see db_init.py),
never the superuser's.

Folders, not single files, are mounted into the services: Docker turns a missing file mount into
an empty folder, which would break the first start (docs/DECISIONS.md).

It also gives the folders web-admin writes to (uploads, the problem store) to the web user when they
are mounted here: Docker creates missing bind-mount folders as root, and web-admin isn't root.
"""

import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path("/secrets")
MASTER = ".master"

# How each secret is made. token_urlsafe(n) gives about 1.3 × n characters.
MAKERS: dict[str, Callable[[], str]] = {
    "db_password": lambda: secrets.token_urlsafe(32),
    # The web roles' database role, grader_app (made by db-init; not a superuser).
    "db_app_password": lambda: secrets.token_urlsafe(32),
    "django_secret_key": lambda: secrets.token_urlsafe(64),
    "worker_token": lambda: secrets.token_urlsafe(32),
}

POSTGRES_UID = 70  # the postgres user in the postgres:*-alpine image (db and its backups)
WEB_UID = 10001  # both web roles (web/Dockerfile)
ROOT_UID = 0  # the worker and the backup service

# Data folders web-admin writes to, if mounted into this container: they belong to the web user.
WEB_FOLDERS = (Path("/data/uploads"), Path("/data/problems"))

# Service folder → (owner, the secrets it gets). web-admin reads the worker token too, so the
# worker's folder belongs to the web user; the worker runs as root and can read it anyway.
CONSUMERS: dict[str, tuple[int, tuple[str, ...]]] = {
    # db-init (root, ops image) reads this folder too: it sets grader_app's password.
    "db": (POSTGRES_UID, ("db_password", "db_app_password")),
    "web": (WEB_UID, ("db_app_password", "django_secret_key")),
    "worker": (WEB_UID, ("worker_token",)),
    "backup": (ROOT_UID, ("db_password",)),
}


def master_secrets(root: Path) -> dict[str, str]:
    """The real values: read them, or make the missing ones (only the first time)."""
    master = root / MASTER
    master.mkdir(mode=0o700, exist_ok=True)
    _own(master, ROOT_UID, 0o700)
    values = {}
    for name, make in MAKERS.items():
        path = master / name
        if path.is_file() and path.read_text(encoding="ascii").strip():
            values[name] = path.read_text(encoding="ascii").strip()
            print(f"secrets-init: {name}: kept")
        else:
            values[name] = make()
            _write(path, values[name], ROOT_UID)
            print(f"secrets-init: {name}: created")
    return values


def give_copies(root: Path, values: dict[str, str]) -> None:
    """Write each service's folder: its secrets, its owner, folder 0500 and files 0400."""
    for folder_name, (uid, names) in CONSUMERS.items():
        folder = root / folder_name
        folder.mkdir(exist_ok=True)
        _own(folder, ROOT_UID, 0o700)  # writable while we fill it
        for name in names:
            _write(folder / name, values[name], uid)
        for old in folder.iterdir():  # a secret this service no longer gets
            if old.name not in names:
                old.unlink()
        _own(folder, uid, 0o500)
        print(f"secrets-init: {folder_name}/: {', '.join(names)}")


def prepare_web_folders() -> None:
    """Hand the mounted web-admin folders to the web user (only the folder itself, 0750)."""
    for folder in WEB_FOLDERS:
        if folder.is_dir() and not folder.is_symlink():
            _own(folder, WEB_UID, 0o750)
            print(f"secrets-init: {folder}: web user")


def _write(path: Path, value: str, uid: int) -> None:
    """Replace `path` with `value` atomically, owned by `uid`, mode 0400. Never follows links."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o400)
    try:
        os.write(descriptor, (value + "\n").encode("ascii"))
        os.fchown(descriptor, uid, uid)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _own(path: Path, uid: int, mode: int) -> None:
    os.chown(path, uid, uid, follow_symlinks=False)
    os.chmod(path, mode)


def main() -> int:
    try:
        root = ROOT
        root.chmod(0o755)
        give_copies(root, master_secrets(root))
        prepare_web_folders()
    except OSError as error:
        print(f"secrets-init: {error}", file=sys.stderr)
        return 1
    print("secrets-init: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
