#!/usr/bin/env bash
# Production dry run: the ZimaOS compose file (deploy/zimaos/grader.compose.yml) with images built
# here under their GHCR names, /DATA/AppData/grader mapped to a temporary folder, and no Cloudflare
# tunnel: a "probe" container on edge_net stands in for it. Prints one PASS/FAIL line per check and
# exits 1 if any failed. Needs internet (the edge fetches Google's keys).
#
# The dev stack may stay up: this runs as its own Compose project, on its own subnet and port 8081.
# Only the dev worker is stopped meanwhile (both workers would use the host's grader-judge cgroup),
# and started again at the end.
#
#   scripts/prod-smoke.sh          run the checks, then remove everything
#   scripts/prod-smoke.sh --keep   leave the stack running afterwards, to look around
set -uo pipefail
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

cd "$(dirname "$0")/.."
REPO="$PWD"
PROJECT=grader-smoke
DATA="$(mktemp -d /tmp/grader-smoke.XXXXXX)"
WORK="$(mktemp -d /tmp/grader-smoke-files.XXXXXX)"
COMPOSE_FILE="$WORK/grader.compose.yml"
OVERRIDE="$WORK/smoke.override.yml"
PUBLIC_HOST="grader.example.org"
ADMIN_HOST="192.0.2.10:8081"
DEV=(docker compose -f "$REPO/deploy/dev/compose.dev.yml")
failures=0

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
check() {  # check "description" command...
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi
}

dc() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" -f "$OVERRIDE" "$@"; }
in_service() { local service="$1"; shift; dc exec -T "$service" "$@"; }
# Python inside a service: in_python <service> <code> [args...]
in_python() { local service="$1" code="$2"; shift 2; dc exec -T "$service" python3 -c "$code" "$@"; }

cleanup() {
  if [ "$KEEP" = 1 ]; then
    echo "Kept running (project $PROJECT, data in $DATA). Stop it with:"
    echo "  docker compose -p $PROJECT -f $COMPOSE_FILE -f $OVERRIDE down; then start the dev worker"
    return
  fi
  dc down --remove-orphans >/dev/null 2>&1
  docker run --rm -v "$DATA:/x" --entrypoint sh "ghcr.io/kit2code/grader-ops:0.1.1" \
    -c 'rm -rf /x/* /x/.[!.]*' >/dev/null 2>&1
  rm -rf "$DATA" "$WORK"
  "${DEV[@]}" start worker >/dev/null 2>&1 || true
}
trap cleanup EXIT

# HTTP from inside a container: prints "<status> <body>" (body cut to 4000 characters).
HTTP='
import sys, urllib.error, urllib.request
method, url, host = sys.argv[1], sys.argv[2], sys.argv[3]
data = sys.argv[4].encode() if len(sys.argv) > 4 and sys.argv[4] else None
headers = {"Host": host} if host else {}
for extra in sys.argv[5:]:
    name, value = extra.split(":", 1)
    headers[name] = value
request = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        print(response.status, response.read().decode(errors="replace")[:4000])
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode(errors="replace")[:4000])
except Exception as error:
    print("ERR", type(error).__name__)
'
status_of() { echo "${1%% *}"; }

echo "== build the images (ghcr.io/kit2code/grader-*:0.1.1)"
scripts/build-images.sh --tag 0.1.1 >/dev/null || { echo "FAIL  image build"; exit 1; }

echo "== start (data in $DATA)"
sed "s#/DATA/AppData/grader#$DATA#g" deploy/zimaos/grader.compose.yml > "$COMPOSE_FILE"
cat > "$OVERRIDE" <<'EOF'
services:
  tunnel:
    profiles: ["never"]     # no Cloudflare in the dry run; the probe plays its part
  probe:
    image: ghcr.io/kit2code/grader-ops:0.1.1
    command: ["sleep", "infinity"]
    networks: [edge_net]
EOF
"${DEV[@]}" stop worker >/dev/null 2>&1 || true
if ! dc up -d --wait --wait-timeout 300 >/dev/null 2>&1; then
  dc ps
  dc logs --tail 30
  echo "FAIL  the stack did not become healthy"
  exit 1
fi
pass "every service healthy"

echo "== zones and paths"
for path in /admin /ADMIN //admin /%61dmin /internal/judge/claim /setup; do
  answer="$(in_python probe "$HTTP" GET "http://edge:8080$path" "$PUBLIC_HOST")"
  if [ "$(status_of "$answer")" = 404 ]; then pass "public $path → 404"; else fail "public $path → $answer"; fi
done
answer="$(in_python probe "$HTTP" GET "http://edge:8080/" "$PUBLIC_HOST")"
[ "$(status_of "$answer")" = 200 ] && pass "public home page" || fail "public home page → ${answer:0:200}"
admin="$(curl -s -H "Host: $ADMIN_HOST" http://127.0.0.1:8081/admin/login/)"
case "$admin" in *csrfmiddlewaretoken*) pass "admin port serves the Django admin login" ;;
  *) fail "admin login page" ;; esac
answer="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $ADMIN_HOST" -X POST http://127.0.0.1:8081/internal/judge/claim)"
[ "$answer" = 404 ] && pass "admin port: /internal → 404" || fail "admin port /internal → $answer"
check "the public port is not published on the host" bash -c '! curl -s -m 3 http://127.0.0.1:8080/'

echo "== no way out"
for service in web web-admin; do
  answer="$(in_python "$service" "$HTTP" GET https://1.1.1.1 "")"
  [ "$(status_of "$answer")" = ERR ] && pass "$service has no internet" || fail "$service reached 1.1.1.1"
done
answer="$(dc exec -T worker python3 -c "$HTTP" GET https://1.1.1.1 "")"
[ "$(status_of "$answer")" = ERR ] && pass "worker has no internet" || fail "worker reached 1.1.1.1"
answer="$(dc exec -T worker python3 -c 'import socket; socket.create_connection(("db", 5432), timeout=5)' 2>&1)"
[ $? -ne 0 ] && pass "worker can't reach db:5432" || fail "worker reached the database"
answer="$(in_python web 'import socket; socket.create_connection(("worker", 22), timeout=5)' 2>&1)"
[ $? -ne 0 ] && pass "public web can't reach the worker" || fail "public web reached the worker"
answer="$(in_python web "$HTTP" GET http://web-admin:8000/healthz "" 2>&1)"
[ "$(status_of "$answer")" = 200 ] || true  # web and web-admin share app_net; zones guard that

echo "== Google egress (:8090)"
answer="$(in_python web "$HTTP" GET http://10.89.91.10:8090/certs "")"
case "$answer" in "200 "*'"keys"'*) pass "web → :8090/certs gets Google's real keys" ;;
  *) fail "web → :8090/certs: ${answer:0:200}" ;; esac
answer="$(in_python web "$HTTP" POST http://10.89.91.10:8090/token "" "grant_type=x")"
case "$(status_of "$answer")" in 400|401) pass "web → :8090/token reaches Google (refused a bad request)" ;;
  *) fail "web → :8090/token: ${answer:0:200}" ;; esac
answer="$(in_python web "$HTTP" GET http://10.89.91.10:8090/other "")"
[ "$(status_of "$answer")" = 404 ] && pass ":8090 other paths → 404" || fail ":8090 /other → $answer"
answer="$(in_python web-admin "$HTTP" POST http://10.89.91.10:8090/token "" "x")"
[ "$(status_of "$answer")" = 404 ] && pass "web-admin → :8090/token → 404" || fail "web-admin /token → $answer"
answer="$(dc exec -T worker python3 -c "$HTTP" GET http://10.89.91.10:8090/certs "")"
[ "$(status_of "$answer")" = ERR ] && pass "worker can't reach :8090" || fail "worker reached :8090"
check "the host can't reach :8090" bash -c '! curl -s -m 3 http://10.89.91.10:8090/certs'

echo "== secrets"
listing() {
  docker run --rm -v "$DATA/secrets:/s" --entrypoint sh ghcr.io/kit2code/grader-ops:0.1.1 -c \
    'cd /s && find . -mindepth 1 -not -path "./.master*" -exec stat -c "%n %u %a" {} + | sort; cat .master/* | sha256sum'
}
first="$(listing)"
expected="./backup 0 500
./backup/db_password 0 400
./db 70 500
./db/db_app_password 70 400
./db/db_password 70 400
./web 10001 500
./web/db_app_password 10001 400
./web/django_secret_key 10001 400
./worker 10001 500
./worker/worker_token 10001 400"
[ "$(echo "$first" | head -n 10)" = "$expected" ] && pass "secrets: owners and modes" || { fail "secrets: owners and modes"; echo "$first"; }
dc restart >/dev/null 2>&1; dc up -d --wait --wait-timeout 300 >/dev/null 2>&1
[ "$(listing)" = "$first" ] && pass "secrets survive a restart unchanged" || fail "secrets changed on restart"

echo "== a submission judged through the edge"
dc cp fixtures/problems/aplusb web-admin:/data/uploads/smoke-aplusb >/dev/null 2>&1
in_service web-admin python manage.py import_problem /data/uploads/smoke-aplusb --visible >/dev/null 2>&1
SESSION_CODE='
from accounts.models import User
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from core.middleware import SESSION_ROLE_KEY
user, _ = User.objects.get_or_create(username="smoke_student", defaults={"display_name": "Smoke"})
session = SessionStore()
session[SESSION_KEY] = str(user.pk)
session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
session[HASH_SESSION_KEY] = user.get_session_auth_hash()
session[SESSION_ROLE_KEY] = "public"
session.create()
print(session.session_key)
'
session_key="$(in_service web python manage.py shell -c "$SESSION_CODE" 2>/dev/null | tail -n 1)"
# Submit through the public edge as the probe (the tunnel's place), like a browser would: the
# session cookie from above, the CSRF cookie the page sets, and the HTTPS Referer/Origin of the site.
# Cookies are handled by hand: Django marks them Secure, and the probe talks plain http to the edge.
SUBMIT='
import re, sys, time, urllib.parse, urllib.request
host, session, source = sys.argv[1], sys.argv[2], sys.argv[3]
cookies = {"g_sess": session}
def call(path, data=None):
    headers = {"Host": host, "Referer": f"https://{host}{path}", "Origin": f"https://{host}",
               "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
    response = urllib.request.urlopen(
        urllib.request.Request("http://edge:8080" + path, data=data, headers=headers), timeout=20)
    for header in response.headers.get_all("Set-Cookie") or []:
        name, value = header.split(";", 1)[0].split("=", 1)
        cookies[name] = value
    return response
page = call("/problems/aplusb/submit").read().decode()
token = re.search(r"name=\"csrfmiddlewaretoken\" value=\"([^\"]+)\"", page).group(1)
body = urllib.parse.urlencode(
    {"csrfmiddlewaretoken": token, "language": "cpp17", "source": source}).encode()
submission = re.search(r"/submissions/(\d+)", call("/problems/aplusb/submit", body).geturl()).group(1)
for _ in range(90):
    fragment = call(f"/submissions/{submission}/results").read().decode()
    if "hx-get" not in fragment:
        accepted = "ผ่าน" in fragment
        if not accepted:  # show what the page says, without the HTML tags
            print(" ".join(re.sub(r"<[^>]+>", " ", fragment).split())[:600])
        print("done", submission, accepted)
        break
    time.sleep(2)
else:
    print("timeout", submission)
'
output="$(in_python probe "$SUBMIT" "$PUBLIC_HOST" "$session_key" "$(cat fixtures/problems/aplusb/solutions/main.cpp)" 2>&1)"
answer="$(echo "$output" | tail -n 1)"
case "$answer" in "done "*" True") pass "aplusb submitted through the public edge and judged AC" ;;
  *) fail "submission through the edge: $answer"; echo "$output" | head -n -1 ;; esac

echo "== the judge under the production profile"
caps="$(in_service worker grep CapEff /proc/self/status)"
case "$caps" in *000001ffffffffff*) fail "worker is privileged ($caps)" ;; *) pass "worker is not privileged ($caps)" ;; esac
# The hardened profile (docs/DECISIONS.md, "Worker profile", "Seccomp filter for sandboxed programs").
caps="$(echo "$caps" | tr -d '[:space:]')"
[ "$caps" = "CapEff:00000000002410cb" ] && pass "worker capabilities: exactly the 8 isolate needs" || fail "worker capabilities: $caps"
answer="$(in_service worker cat /proc/self/cgroup)"
[ "$answer" = "0::/worker" ] && pass "worker in its own cgroup namespace (0::/worker)" || fail "worker cgroup: $answer"
answer="$(in_service worker sh -c 'ls /sys/fs/cgroup | grep -c -e system.slice -e docker -e init.scope' || true)"
[ "$answer" = 0 ] && pass "worker sees none of the host's cgroups" || fail "worker sees host cgroups ($answer)"
answer="$(in_service worker grep -E '^(Seccomp|NoNewPrivs):' /proc/1/status | tr -s '[:space:]' ' ')"
[ "$answer" = "NoNewPrivs: 1 Seccomp: 2 " ] && pass "worker: Docker's seccomp filter and no-new-privileges" || fail "worker seccomp: $answer"
in_service worker sh -c 'test -x /usr/local/libexec/grader/box-exec && ! /usr/local/libexec/grader/box-exec 2>/dev/null' \
  && pass "box launcher present (and refuses to run nothing)" || fail "box launcher"
answer="$(in_service worker judge-health 2>&1 && echo ok)"
[ "$answer" = ok ] && pass "worker healthcheck (judge-health)" || fail "worker healthcheck: $answer"
answer="$(in_service worker cat /sys/fs/cgroup/grader-judge/cpuset.cpus)"
[ "$answer" = "4-5" ] && pass "sandboxes pinned to CPUs 4-5" || fail "sandbox CPUs: $answer"
# The real self-test takes well under a minute; 10 minutes means something hangs.
answer="$(timeout 600 docker compose -p "$PROJECT" -f "$COMPOSE_FILE" -f "$OVERRIDE" exec -T worker judge-selftest 2>&1)"
if [ $? -eq 0 ]; then pass "self-test: every hostile program contained"
else fail "self-test"; echo "$answer" | grep -B2 -A6 '"ok": false' | head -40; fi

echo "== backups"
in_service web-admin python manage.py shell -c "from core.models import BackupRun; BackupRun.objects.create()" >/dev/null 2>&1
file=""
for _ in $(seq 1 60); do
  file="$(in_service web-admin python manage.py shell -c "from core.models import BackupRun; r=BackupRun.objects.order_by('-pk').first(); print(r.status, r.file_name)" 2>/dev/null | tail -n 1)"
  case "$file" in done*|failed*) break ;; esac
  sleep 2
done
case "$file" in "done "*) pass "back up now → ${file#done }" ;; *) fail "back up now: $file" ;; esac
dump="${file#done }"
in_service db createdb -U grader scratch_restore >/dev/null 2>&1
restored="$(in_service backup sh -c "PGPASSWORD=\$(cat /run/secrets/backup/db_password) pg_restore -h db -U grader -d scratch_restore /backups/$dump && PGPASSWORD=\$(cat /run/secrets/backup/db_password) psql -h db -U grader -d scratch_restore -At -c 'select count(*) from judging_submission'" 2>&1 | tail -n 1)"
[ "${restored:-0}" -ge 1 ] 2>/dev/null && pass "pg_restore into a scratch database: $restored submission(s)" || fail "restore: $restored"
in_service db dropdb -U grader scratch_restore >/dev/null 2>&1

echo "== web-admin restart"
dc kill web-admin >/dev/null 2>&1
dc up -d --wait --wait-timeout 180 web-admin >/dev/null 2>&1 && pass "killed web-admin came back healthy" || fail "web-admin did not come back"
logs="$(dc logs web-admin 2>&1)"
applied="$(echo "$logs" | grep -c '  Applying ')"
duplicates="$(in_service db psql -U grader -d grader -At -c 'select count(*) from (select app, name from django_migrations group by app, name having count(*) > 1) d')"
[ "$duplicates" = 0 ] && pass "no migration recorded twice (applied lines in logs: $applied)" || fail "duplicate migrations: $duplicates"
case "$(dc logs web 2>&1)" in *"Applying "*) fail "the public web role ran a migration" ;; *) pass "only web-admin migrates" ;; esac
# The web roles log in as grader_app, a plain role that owns the tables (docs/DECISIONS.md).
answer="$(in_service db psql -U grader -d grader -At -c "select rolsuper, rolcreatedb, rolcreaterole from pg_roles where rolname = 'grader_app'")"
[ "$answer" = "f|f|f" ] && pass "grader_app is a plain database role" || fail "grader_app flags: $answer"
answer="$(in_service db psql -U grader -d grader -At -c "select count(*) from pg_tables where schemaname = 'public' and tableowner <> 'grader_app'")"
[ "$answer" = 0 ] && pass "grader_app owns every table (migrations ran as it)" || fail "tables not owned by grader_app: $answer"
answer="$(in_service web-admin python -c 'import django, os; os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings"); django.setup(); from django.db import connection; c = connection.cursor(); c.execute("select current_user, usesuper from pg_user where usename = current_user"); print(*c.fetchone())' 2>&1 | tail -n 1)"
[ "$answer" = "grader_app False" ] && pass "web-admin logs in as grader_app" || fail "web-admin database user: $answer"

echo
if [ "$failures" -eq 0 ]; then echo "prod-smoke: all checks passed"; else echo "prod-smoke: $failures check(s) failed"; exit 1; fi
