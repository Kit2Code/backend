#!/bin/sh
# Start-up for both web roles. Never assumes start order: it retries until the database and the
# secret files are there. Only the admin role runs migrations; the public role waits for them.
set -eu

if [ "${GRADER_ROLE:-}" = "admin" ]; then
  until python manage.py migrate --noinput; do
    echo "web-admin: database not ready, retrying in 2 s"
    sleep 2
  done
else
  until python manage.py migrate --check >/dev/null 2>&1; do
    echo "web: waiting for the database and migrations, retrying in 2 s"
    sleep 2
  done
fi

exec "$@"
