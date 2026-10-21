#!/usr/bin/env bash
# Run every code check: translations, lint, formatting, types and tests. CI runs the same steps.
# Needs gettext (msgfmt) for the translations: `sudo apt-get install gettext`.
# Tests that touch the database need Postgres: start the dev stack with ./init.sh first.
# Exit code 0 means every check passed; each failing step is listed at the end.
set -uo pipefail
cd "$(dirname "$0")/.."

failed=()

# run NAME COMMAND...: run one step, remember it if it fails, and keep going.
run() {
  local name="$1"
  shift
  echo "==> $name"
  if ! "$@"; then
    failed+=("$name")
  fi
}

# The tests need the compiled translations (.mo files are built, never committed).
run "translations"  uv run --directory web python manage.py compilemessages -l th -l en -v 0
run "ruff check"    uv run ruff check .
run "ruff format"   uv run ruff format --check .
run "mypy"          uv run mypy .
run "pytest"        uv run pytest -q

if [ "${#failed[@]}" -eq 0 ]; then
  echo "check: all passed"
  exit 0
fi
echo "check: failed: ${failed[*]}"
exit 1
