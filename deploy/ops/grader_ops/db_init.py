"""`db-init`: make the database role the web site uses, and give it the grader's tables.

Runs at every start of the app, after the database is up and before the web roles. The web roles
log in as `grader_app`, a plain role (not a superuser: it can't run shell commands through the
database, read the server's files or make other roles). It owns the database and its tables, so
web-admin's migrations still work. The superuser `grader` is left for the database itself and
the backups.

Each run: make the role (or set its password again, if secrets-init made a new one), make it the
owner of the database, and hand it every table, view and sequence the superuser still owns (from an
older version, or a restored backup). docs/DECISIONS.md, "A plain database role for the web site".
"""

import os
import subprocess
import sys
import time
from pathlib import Path

PG_BIN = Path("/usr/local/bin")
SECRETS = Path("/run/secrets/db")
WAIT_SECONDS = 300

# The SQL is fixed text: the role is grader_app, the superuser and the database are grader. The
# new password reaches psql in an environment variable (\getenv), never inside the SQL or an argv.
ROLE_EXISTS = "SELECT 1 FROM pg_roles WHERE rolname = 'grader_app';"
FLAGS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'password'"
CREATE_ROLE = "CREATE ROLE grader_app WITH " + FLAGS + ";"
ALTER_ROLE = "ALTER ROLE grader_app WITH " + FLAGS + ";"
HAND_OVER = """
\\getenv password GRADER_APP_PASSWORD
{role}
ALTER DATABASE grader OWNER TO grader_app;
DO $$
DECLARE
    item record;
BEGIN
    -- Tables first: a sequence that belongs to a table changes owner with it.
    FOR item IN
        SELECT c.oid, c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relowner = 'grader'::regrole
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
        ORDER BY c.relkind = 'S'
    LOOP
        IF (SELECT relowner FROM pg_class WHERE oid = item.oid) = 'grader'::regrole THEN
            EXECUTE format('ALTER %s public.%I OWNER TO grader_app',
                CASE item.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                    WHEN 'S' THEN 'SEQUENCE' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,
                item.relname);
        END IF;
    END LOOP;
END $$;
"""


class DbInitError(Exception):
    pass


def read_password(name: str) -> str:
    value = (SECRETS / name).read_text(encoding="ascii").strip()
    if not value:
        raise DbInitError(f"{name} is empty")
    return value


def psql(script: str, superuser_password: str, app_password: str = "") -> str:
    """Run SQL as the superuser (script on stdin; passwords only in the environment)."""
    env = dict(os.environ)
    env.setdefault("PGHOST", "db")
    env.update(PGUSER="grader", PGDATABASE="grader", PGPASSWORD=superuser_password,
               GRADER_APP_PASSWORD=app_password)  # fmt: skip
    run = subprocess.run(  # noqa: S603 - fixed argv list
        [str(PG_BIN / "psql"), "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"],
        input=script, capture_output=True, text=True, env=env, timeout=60, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise DbInitError(run.stderr.strip()[-2000:])
    return run.stdout.strip()


def wait_for_database(superuser_password: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            psql("SELECT 1;", superuser_password)
            return
        except DbInitError as error:
            if time.monotonic() > deadline:
                raise
            print(f"db-init: waiting for the database ({error})")
            time.sleep(2)


def set_up(superuser_password: str, app_password: str) -> None:
    exists = psql(ROLE_EXISTS, superuser_password) == "1"
    script = HAND_OVER.replace("{role}", ALTER_ROLE if exists else CREATE_ROLE)
    psql(script, superuser_password, app_password)
    print(f"db-init: role grader_app {'updated' if exists else 'created'}; it owns the tables")


def main() -> int:
    try:
        superuser_password = read_password("db_password")
        app_password = read_password("db_app_password")
        wait_for_database(superuser_password)
        set_up(superuser_password, app_password)
    except (OSError, DbInitError) as error:
        print(f"db-init: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
