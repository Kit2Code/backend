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
mkdir -p .devdata/secrets/db .devdata/secrets/web .devdata/secrets/worker .devdata/secrets/fake-oidc .devdata/problems .devdata/uploads \
  .devdata/judge-cache
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
if [ ! -s .devdata/secrets/worker/worker_token ]; then
  new_secret > .devdata/secrets/worker/worker_token
  echo "init: created a dev worker token"
fi
if [ ! -s .devdata/secrets/fake-oidc/client_secret ]; then
  new_secret > .devdata/secrets/fake-oidc/client_secret
  echo "init: created a client secret for the fake Google sign-in"
fi
umask 022

uv sync --quiet
# The browser for the end-to-end tests (downloaded once; its system libraries need
# `sudo uv run playwright install-deps chromium` once per machine).
uv run playwright install chromium
# Compiled translations for the web roles (they run the code mounted from here). Needs gettext.
uv run --directory web python manage.py compilemessages -l th -l en -v 0

export DEV_UID DEV_GID
DEV_UID="$(id -u)"
DEV_GID="$(id -g)"
"${COMPOSE[@]}" up -d --build --wait --remove-orphans
# The fake Google sign-in client (same client ID as fake-oidc in the compose file).
"${COMPOSE[@]}" exec -T web-admin python manage.py set_google_client \
  --client-id grader-dev.apps.example < .devdata/secrets/fake-oidc/client_secret
"${COMPOSE[@]}" ps
echo "init: dev stack is up (public http://127.0.0.1:8000, admin http://127.0.0.1:8001)"
