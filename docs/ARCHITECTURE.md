# Architecture

This page explains the parts of the grader and how they talk to each other. Reasons for the bigger
choices are in [DECISIONS.md](DECISIONS.md).

The project is early: parts marked *(planned)* are designed but not built yet.

## The big picture

```
 Internet ─► Cloudflare ─► tunnel ─► edge :8080 ─► web        (GRADER_ROLE=public)
                                       blocks /admin* /internal* /setup*   │
            Google ◄── edge :8090 (two fixed paths) ◄── web (sign-in)     │
 Home LAN ───────────────────────────► edge :8081 ─► web-admin (GRADER_ROLE=admin)
                                       blocks /internal*                  │   ▲
                                                                          │   │ /internal/* (token)
                         db (Postgres) ◄── both web roles, backup         │   │
                                                                 worker ──┘───┘ (judge_net only)
```

Everything runs as one Docker Compose app on a home server that also runs CMS.

| Service | What it does |
|---|---|
| **db** | PostgreSQL. Holds users, problems, submissions, and also the judge queue (no Redis). |
| **web** | The Django app in the **public** role: the practice site. |
| **web-admin** | The same Django image in the **admin** role: Django admin, admin pages, and the internal judge API. |
| **worker** | The judge. Takes jobs from web-admin, compiles and runs submissions inside isolate, reports results. |
| **edge** | Caddy. The single entry point; routes each port to one role, sets the zone header, blocks paths that don't belong there, and is the public web role's only way to Google. Dev uses stock Caddy with `deploy/dev/edge/Caddyfile`; the production image is *(planned)*. |
| **fake-oidc** (dev only) | A fake "Sign in with Google" (`deploy/dev/fake-oidc/`) for development and tests. |
| **tunnel** *(planned)* | cloudflared. Carries public traffic from Cloudflare to the edge; no open ports on the home router. |
| **secrets-init**, **backup** *(planned)* | One-shot secret generation, and nightly database backups. |

## One web image, two roles

The web app is a single Docker image. The environment variable `GRADER_ROLE` chooses what it is:

- `public` loads `web/config/urls_public.py`: practice pages only. The Django admin, admin pages,
  setup pages and the internal API are **not in its URL configuration at all**, so a bug in the
  public site cannot reach them: as far as the public container knows, those URLs don't exist.
- `admin` loads `web/config/urls_admin.py`: the Django admin, admin pages and the internal API.

`web/config/env.py` reads the role and refuses to start if it is anything other than `public` or
`admin`. Each role accepts only its own host name (`PUBLIC_BASE_URL` or `ADMIN_BASE_URL`).

As a second layer, the edge adds an `X-Edge-Zone` header (`public` or `admin`, replacing any copy
a client sent), and `core/middleware.py` checks it:

- `EdgeZoneMiddleware`: the header must equal the role, or the answer is 404 (not 403, so nothing
  is advertised). Only `/healthz` works without it, for Docker's healthcheck inside the container.
  `/internal/*` (admin role only) is the opposite: it must have **no** zone header (the worker
  calls it directly, never through the edge) and `Authorization: Bearer <worker token>`.
- `RoleSessionMiddleware`: every session is marked with the role that made it (at login, or when a
  view first stores something). A session that shows up in the other role, or has no mark, is
  deleted. Each role also has its own cookie names (`g_sess`/`g_csrf` public,
  `g_admin`/`g_admin_csrf` admin), so a browser never mixes them up by itself; the mark catches a
  session key copied on purpose.

## Networks

| Network | Internet access | Who is on it |
|---|---|---|
| `app_net` | no (`internal: true`) | edge, web, web-admin |
| `db_net` | no | db, web, web-admin, backup |
| `judge_net` | no | web-admin, worker |
| `edge_net` *(planned)* | yes | edge, tunnel |

The web roles and the worker never need the internet while running, so their networks have no way
out. The worker is only on `judge_net`: it cannot reach the database at all.

In the dev stack there is one extra network, `dev_ports`, so that your machine can reach Postgres
and the edge on `127.0.0.1` (`:8000` public, `:8001` admin). The web roles and the worker are not
on it, so, as in production, the only way to them is through the edge.

**The one way out: Google sign-in.** The edge has a third listener, `:8090`, on `app_net` only. It
forwards exactly `POST /token` and `GET /certs` to Google (in dev: to `fake-oidc`), caps request
bodies at 16 KB, and answers 404 to anything else. It listens on the edge's `app_net` address only
and serves only the public web role's address (the dev compose gives `app_net` a fixed subnet for
this), so web-admin, the worker and the host can't use it. An SSRF bug in the public site could
reach nothing but Google's token endpoint. Caddy can't cap response sizes, so the web side reads
those two responses with a 64 KB limit. In dev, `fake-oidc` sits on its own normal network
(`fake_google_net`, standing in for the internet) that only the edge joins.

## Secrets

Secrets (database password, Django secret key, worker token) are never environment
variables and never in git. Each container gets a **directory** of secret files mounted read-only
(for the web roles, `/run/secrets/web/`), and the code reads one file per secret. The directory is
named by `GRADER_SECRETS_DIR`. A missing or empty file stops the container at start-up with a
clear message.

Each service gets only the secrets it needs. Directories are mounted instead of single files
because Docker silently creates an empty directory when a mounted file doesn't exist yet. The
admin role also mounts the worker's folder (named by `GRADER_WORKER_SECRETS_DIR`) to read the
worker token; the public role never has it.

The Google client secret is typed into the admin site (a management command for now,
`set_google_client`, reading it from stdin) and kept in the database **encrypted**
(`core/crypto.py`: Fernet, with a key derived from the Django secret key by HKDF). A database
backup alone doesn't reveal it.

In development, `./init.sh` creates random dev secrets under `.devdata/secrets/` (gitignored).

## Database migrations

Only **web-admin** runs `manage.py migrate` when it starts, retrying until the database is up. The
public **web** container waits, retrying `manage.py migrate --check`, until the schema is current.
Two containers running migrations at the same time could break a migration run.

The user model is our own (`accounts.User`) from the very first migration, because Django can't
switch to a custom user model later without a painful data migration.

## Data model

| App | Models |
|---|---|
| `accounts` | `User` (username, display name, school; students have no usable password), `GoogleIdentity` (issuer + Google account id, one per user) |
| `problems` | `Problem` (slug, titles, visible or hidden, current version, tags), `ProblemVersion` (one folder in the store, its hash and import report), `Tag` |
| `judging` | `Submission`, `TestResult`, `JudgeJob` (the queue), `Worker`, `UserProblemBest` (best score per user and problem, for the ranking) *(used from the submissions milestone on)* |
| `core` | `SiteSetting` (one row: grading paused, time scale, registration open, announcement, Google client), `PauseWindow`, `AuditLog`, `RateLimitCounter`, `BackupRun`, `SetupToken` |

Two queries get indexes made for them, checked with `EXPLAIN` in `web/tests/test_models.py`: the
worker's job claim (a partial index on queued jobs, by priority then age) and the ranking (a
covering index on the per-user best scores).

## Accounts and sign-in

- **Students** sign in on the public site with Google only (`accounts/google.py`), using the OpenID
  Connect authorization-code flow:
  1. `/auth/google/start` makes a random `state`, `nonce` and PKCE verifier, keeps them in the
     session, and redirects the browser to Google's sign-in page asking for the `openid` scope only.
  2. Google sends the browser back to `/auth/google/callback` with `state` and a one-time `code`.
     The attempt is taken out of the session (so it works once), its `state` must match, and it must
     be under 10 minutes old.
  3. The code, the verifier and our client secret are swapped for an ID token through edge `:8090`.
     The token must be signed (RS256 only) by one of Google's keys (fetched through `:8090`), with
     the right issuer, audience (our client ID), expiry, issue time and nonce.
  4. The account is identified by Google's issuer and account id (`sub`) only, stored in
     `GoogleIdentity`. We never ask for email, name or photo.
  5. A new Google account first sees `/auth/welcome` to choose a username, display name and
     optional school (while it waits, the identity lives only in the session, for 10 minutes).
     `login()` gives the session a new key, and only a path on our own site is accepted as `next`.
- **Staff** have a username and password (Argon2) and log in only on the admin site, through the
  Django admin's login page (`config/admin.py` rate-limits it).
- **Log out everywhere** raises `User.session_version`, which is part of Django's session hash, so
  every other session of that user stops working on its next request.
- **Rate limits** (`core/ratelimit.py`): one Postgres table of counters per fixed time window,
  updated with a single `INSERT … ON CONFLICT DO UPDATE … RETURNING count`, so it is exact across
  processes. The key is a hash, so user input never reaches the table as text. The client address
  is Cloudflare's `Cf-Connecting-Ip` on the public site, otherwise the last `X-Forwarded-For` entry
  (written by the edge), otherwise the TCP peer.

## Pages, languages and static files

- Templates: `core/templates/base.html` (Pico CSS, classless, plus `core/static/site.css`) and one
  folder per app. Static files, including the vendored Pico CSS and Thai font
  (`core/static/vendor/README.md` lists versions and licences), are served by WhiteNoise.
- **Languages:** Thai is the default and English is one click away (Django's `set_language`, POST
  with CSRF; the choice is a cookie). The browser's own language preference is ignored on purpose
  (`ThaiByDefaultMiddleware`). Every user-visible string goes through Django's translation
  functions; `web/locale/{th,en}/LC_MESSAGES/django.po` hold the texts. The compiled `.mo` files
  are built, never committed: `init.sh`, `scripts/check.sh`, CI and the image build all run
  `compilemessages` (it needs GNU gettext).
- **Problem pages** (`problems/`): the list and the problem page read the current version's
  `problem.yaml` from the store (cached: versions never change). Markdown statements are rendered
  with markdown-it-py (raw HTML off) and then cleaned by nh3; that cleaned HTML is the only thing
  marked safe in templates. Hidden problems are 404 for everyone in the public role.
- **Problem files** (`problems/files.py`, `/problems/<slug>/files/<name>`): only an allow-list is
  served: statement PDFs, everything under `attachments/`, the sample tests, and grader files marked
  downloadable. A name must be on the list; then `problems/store.py` refuses `..`, absolute paths,
  a symlink anywhere in the path, and anything that isn't a regular file. Tests, solutions,
  checkers, other grader files and `problem.yaml` are never served.

## The judge

The worker image builds [isolate](https://github.com/ioi/isolate) from the official git tag (2.7),
checked against a pinned commit. isolate runs each program in a "box" with no network, a non-root
user, and limits on CPU time, wall time, memory, processes, file size and output.

The server also runs CMS, which has its own isolate. To never collide with it, ours uses its own
namespace (`judge/worker/isolate.base`):

| | CMS's isolate | ours |
|---|---|---|
| cgroup root | `/sys/fs/cgroup` | `/sys/fs/cgroup/grader-judge` |
| sandbox user/group ids | from 60000 | from 70000 |
| box ids | from 0 | 100–109 |

The worker's entrypoint (`judge/worker/entrypoint.sh`, which runs `grader_worker/setup.py`) creates
the `grader-judge` cgroup and turns on the `cpuset`, `memory` and `pids` controllers isolate needs.
It also caps the total memory of all sandboxes (`JUDGE_MEM_TOTAL`, no swap) and pins each box to
its own CPU (`JUDGE_BOX_CPUS`).

isolate needs `CAP_SYS_ADMIN` and write access to the host's cgroup tree, so the worker container is
privileged in dev; production is meant to use a narrower profile (only `SYS_ADMIN` added, cgroup
tree mounted read-write; see DECISIONS.md, "Worker container security profile"). isolate itself is
not setuid, because the worker already runs as root. Either way it is
the most dangerous container, which is why it has no internet, no route to the database and no
database password.

`judge-env` (`grader_worker/env_report.py`) prints an **environment report** as JSON: isolate and
kernel versions, the cgroup type and whether it is writable, `memory.max`/`memory.swap.max` of the
judge cgroup, the container's effective capabilities, seccomp mode and AppArmor label, the
box → CPU map with each judge CPU's SMT siblings and P/E-core kind, warnings (judge CPUs sharing a
core, P/E mix, swap), and the read-only `isolate-check-environment` output. It only reads. The dev
result is kept in `docs/env-dev.json`. *(planned)* The worker sends it to web-admin at start and
every 10 minutes, for the admin System page, since the home server has no terminal.

*(planned)* The worker **polls** web-admin's `/internal/*` API over `judge_net`: it claims a job,
sends heartbeats and progress, and posts the result. The job carries everything needed (source code,
problem version), so the worker never touches the database. Request and response bodies are
defined once in `common/grader_common/` and shared by both sides.

Notes for the web side, from the sandbox audit:
- The worker reports exact time, wall time and memory per test. A submission could encode facts
  about a hidden test's input in how long it runs or how much it allocates, so pages should round
  these values (and submissions are rate-limited anyway).
- The shared memory budget has no queue: a waiting large step is retried every 50 ms, so with a tight
  `JUDGE_MEM_TOTAL` a big job can wait longer than a fair queue would make it.

## Problems: format, importer and store

A problem is a folder with a `problem.yaml` plus statements, tests, and optionally a checker,
graders and reference solutions (details for problem setters: [PROBLEM_FORMAT.md](PROBLEM_FORMAT.md)).

- **Format** (`common/grader_common/problem_format.py`): pydantic models are the single definition of
  `problem.yaml`, used by the importer now and by the web app and the worker later.
- **Scoring** (`common/grader_common/scoring.py`): CMS's Sum and GroupMin as pure functions. The
  worker will call `score()` with each test's outcome.
- **Importer** (`common/grader_common/importer/`), used by the `grader-import` command now and by the
  admin upload page later:
  1. `archive.py` unpacks the zip (or copies the folder) into a private staging folder, refusing
     anything unsafe (links, `..`, special files, zip bombs).
  2. `native.py` takes our format as it is; `italy.py` converts a CMS Italian-format task into it.
  3. `native.py` checks the result: `problem.yaml`, statements, test pairs, and that every file it
     names exists.
  4. `store.py` hashes the content and either finds the same version already stored or renames the
     staging folder into place in one step.
- **Registering** (`web/problems/registry.py`): after an import, a `Problem` and `ProblemVersion`
  row point at the stored folder (titles and tags are copied, so the list needs no file reads).
  `manage.py import_problem <zip or folder> [--visible]` does both; `--visible` publishes at once
  and is for dev and tests only (the admin import page will validate first).
- **Store** (`/data/problems` in containers, `.devdata/problems` in dev): `<slug>/v<NNN>-<hash8>/`.
  Versions never change after they are written, so a rejudge always sees the files the submission
  was judged on, and the worker can cache by version. The public web role mounts the store
  read-only; only web-admin writes to it.

## Code layout

| Path | Contents |
|---|---|
| `pyproject.toml`, `uv.lock` | uv workspace root: ties the three Python packages together; one lockfile; ruff, mypy and pytest settings |
| `web/` | Django project: `config/` (settings, the two URL configurations, the admin site), `accounts/` (users, Google sign-in), `core/` (zones, rate limits, site settings, base template), `problems/` (pages, files, store), `judging/` (submission and queue models), `locale/` (translations), `tests/` (`e2e/`: browser tests), `Dockerfile` |
| `judge/worker/` | the worker package, its `Dockerfile`, `isolate.conf` and entrypoint |
| `common/grader_common/` | code shared by web and worker: problem format, scoring, importer (`grader-import`) |
| `fixtures/` | small made-up problems for tests (native and CMS Italian format) |
| `deploy/dev/compose.dev.yml` | the development stack (db, web, web-admin, worker, edge, fake-oidc) |
| `deploy/dev/edge/`, `deploy/dev/fake-oidc/` | the dev edge's Caddyfile; the fake Google sign-in |
| `deploy/tests/` | tests that drive the running dev stack (the `:8090` listener, sign-in end to end) |
| `init.sh` | creates dev secrets and starts the dev stack |
| `scripts/` | `check.sh` (lint, types, tests) and `smoke.sh` (is the running stack healthy?) |
| `.github/workflows/ci.yml` | CI: the same checks, plus Django deploy checks and a secret scan |
