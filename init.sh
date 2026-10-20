#!/usr/bin/env bash
# Start (or update) the dev stack. Safe to run any number of times:
# it only creates what is missing, then rebuilds and starts the containers and waits until
# they are healthy. Data lives in ./.devdata (gitignored).
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f deploy/dev/compose.dev.yml)

# Random dev-only secrets, made once. Production secrets are made on the server, never here.
# 48 random bytes give 64 characters; Django's deploy check wants a SECRET_KEY of 50 or more.
new_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

umask 077
mkdir -p .devdata/secrets/db .devdata/secrets/web .devdata/problems .devdata/uploads
if [ ! -s .devdata/secrets/db/db_password ]; then
  new_secret > .devdata/secrets/db/db_password
  echo "init: created a dev database password"
fi
# The web roles get their own copy, like the per-service secret folders in production.
cp .devdata/secrets/db/db_password .devdata/secrets/web/db_password
if [ ! -s .devdata/secrets/web/django_secret_key ]; then
  new_secret > .devdata/secrets/web/django_secret_key
  echo "init: created a dev Django secret key"
fi
umask 022

uv sync --quiet

export DEV_UID DEV_GID
DEV_UID="$(id -u)"
DEV_GID="$(id -g)"
"${COMPOSE[@]}" up -d --build --wait --remove-orphans
"${COMPOSE[@]}" ps
echo "init: dev stack is up (public http://127.0.0.1:8000, admin http://127.0.0.1:8001)"
