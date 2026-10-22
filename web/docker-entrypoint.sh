#!/bin/sh
# Start-up for both web roles. Never assumes start order: it retries until the database and the
# secret files are there. Only the admin role runs migrations (and makes the first-run setup
# link); the public role waits for them.
set -eu

if [ "${GRADER_ROLE:-}" = "admin" ]; then
  until python manage.py migrate --noinput; do
    echo "web-admin: database not ready, retrying in 2 s"
    sleep 2
  done
  # While no superuser exists, print a fresh one-time setup link (and save it in /data/uploads).
  python manage.py setup_link || echo "web-admin: could not make the setup link (see the error above)"
else
  until python manage.py migrate --check >/dev/null 2>&1; do
    echo "web: waiting for the database and migrations, retrying in 2 s"
    sleep 2
  done
fi

exec "$@"
