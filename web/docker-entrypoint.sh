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
  # Show why it is still waiting (the last line of the error): a missing secret or a permissions
  # problem would otherwise look like a slow database forever.
  until output=$(python manage.py migrate --check 2>&1); do
    reason=$(printf '%s\n' "$output" | grep . | tail -n 1)
    echo "web: waiting for the database and migrations (${reason:-web-admin has not migrated yet}), retrying in 2 s"
    sleep 2
  done
fi

# `serve` (the production image's command): gunicorn, sized per role. Threads suit Django here: most
# time is spent waiting for Postgres. The admin role gets a long timeout for big problem imports.
if [ "${1:-}" = "serve" ]; then
  if [ "${GRADER_ROLE:-}" = "admin" ]; then
    set -- gunicorn --workers 1 --threads 4 --timeout 900
  else
    set -- gunicorn --workers 2 --threads 4 --timeout 30
  fi
  # No control socket: it would need a home folder the app user doesn't have.
  set -- "$@" --worker-class gthread --bind 0.0.0.0:8000 --worker-tmp-dir /dev/shm --no-control-socket \
    --max-requests 2000 --max-requests-jitter 200 config.wsgi:application
fi

exec "$@"
