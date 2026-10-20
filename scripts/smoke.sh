#!/usr/bin/env bash
# Smoke test for the dev stack (start it first with ./init.sh).
# Checks that both web roles answer, that /admin/ exists only in the admin role,
# and that isolate inside the worker works with our box ids and cgroup root.
# Exit code 0 means every check passed.
set -uo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f deploy/dev/compose.dev.yml)
PUBLIC_URL="http://127.0.0.1:8000"
ADMIN_URL="http://127.0.0.1:8001"
failures=0

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; failures=$((failures + 1)); }

# expect_body NAME URL EXPECTED_BODY
expect_body() {
  local body
  body="$(curl -fsS --max-time 5 "$2" 2>/dev/null)"
  if [ "$body" = "$3" ]; then pass "$1 = $body"; else fail "$1 (got '${body:-no answer}', want '$3')"; fi
}

# expect_status NAME URL ALLOWED_CODES...
expect_status() {
  local name="$1" url="$2" code
  shift 2
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)"
  for want in "$@"; do
    if [ "$code" = "$want" ]; then pass "$name -> $code"; return; fi
  done
  fail "$name (got ${code:-no answer}, want $*)"
}

expect_body   "public /healthz" "$PUBLIC_URL/healthz" "ok"
expect_body   "admin  /healthz" "$ADMIN_URL/healthz" "ok"
expect_status "public /admin/"  "$PUBLIC_URL/admin/" 404
expect_status "admin  /admin/"  "$ADMIN_URL/admin/" 200 302

# isolate --version prints e.g. "The process isolator 2.7"; we need >= 2.6.
version_line="$("${COMPOSE[@]}" exec -T worker isolate --version 2>/dev/null | head -n 1)"
version="$(printf '%s' "$version_line" | grep -oE '[0-9]+\.[0-9]+' | head -n 1)"
if [ -n "$version" ] && printf '2.6\n%s\n' "$version" | sort -V -C; then
  pass "isolate --version: $version_line"
else
  fail "isolate --version >= 2.6 (got '${version_line:-no answer}')"
fi

if "${COMPOSE[@]}" exec -T worker isolate --cg -b 100 --init >/dev/null 2>&1; then
  pass "isolate --cg -b 100 --init"
else
  fail "isolate --cg -b 100 --init"
fi
if "${COMPOSE[@]}" exec -T worker isolate --cg -b 100 --cleanup >/dev/null 2>&1; then
  pass "isolate --cg -b 100 --cleanup"
else
  fail "isolate --cg -b 100 --cleanup"
fi

if [ "$failures" -eq 0 ]; then
  echo "smoke: all checks passed"
  exit 0
fi
echo "smoke: $failures check(s) failed"
exit 1
