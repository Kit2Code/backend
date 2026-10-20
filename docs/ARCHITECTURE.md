# Architecture

This page explains the parts of the grader and how they talk to each other. Reasons for the bigger
choices are in [DECISIONS.md](DECISIONS.md).

The project is early: parts marked *(planned)* are designed but not built yet.

## The big picture

```
 Internet ─► Cloudflare ─► tunnel ─► edge :8080 ─► web        (GRADER_ROLE=public)
                                       blocks /admin* /internal* /setup*   │
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
| **edge** *(planned)* | Caddy. The single entry point; routes each port to one role and blocks paths that don't belong there. |
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

*(planned)* As a second layer, the edge adds an `X-Edge-Zone` header (and removes any copy sent by
a client), and middleware checks that it matches the role. Each role uses its own session cookie,
and a session made in one role is refused in the other.

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
and both web roles on `127.0.0.1`. The worker is not on it.

## Secrets

Secrets (database password, Django secret key, *(planned)* worker token) are never environment
variables and never in git. Each container gets a **directory** of secret files mounted read-only
(for the web roles, `/run/secrets/web/`), and the code reads one file per secret. The directory is
named by `GRADER_SECRETS_DIR`. A missing or empty file stops the container at start-up with a
clear message.

Each service gets only the secrets it needs. Directories are mounted instead of single files
because Docker silently creates an empty directory when a mounted file doesn't exist yet.

In development, `./init.sh` creates random dev secrets under `.devdata/secrets/` (gitignored).

## Database migrations

Only **web-admin** runs `manage.py migrate` when it starts, retrying until the database is up. The
public **web** container waits, retrying `manage.py migrate --check`, until the schema is current.
Two containers running migrations at the same time could break a migration run.

The user model is our own (`accounts.User`) from the very first migration, because Django can't
switch to a custom user model later without a painful data migration.

## The judge

The worker image builds [isolate](https://github.com/ioi/isolate) from the official git tag (2.7),
checked against a pinned commit. isolate runs each program in a "box" with no network, a non-root
user, and limits on CPU time, wall time, memory, processes, file size and output.

The server also runs CMS, which has its own isolate. To never collide with it, ours uses its own
namespace (`judge/worker/isolate.conf`):

| | CMS's isolate | ours |
|---|---|---|
| cgroup root | `/sys/fs/cgroup` | `/sys/fs/cgroup/grader-judge` |
| sandbox user/group ids | from 60000 | from 70000 |
| box ids | from 0 | 100–109 |

The worker's entrypoint (`judge/worker/entrypoint.sh`) creates the `grader-judge` cgroup and turns on
the `cpuset`, `memory` and `pids` controllers isolate needs. *(planned)* It will also cap the total
memory of all sandboxes and pin each box to its own CPU.

isolate needs to write to the host's cgroup tree, so the worker container is privileged. That makes
it the most dangerous container, which is why it has no internet, no route to the database and no
database password.

*(planned)* The worker **polls** web-admin's `/internal/*` API over `judge_net`: it claims a job,
sends heartbeats and progress, and posts the result. The job carries everything needed (source code,
problem version), so the worker never touches the database. Request and response bodies are
defined once in `common/grader_common/` and shared by both sides.

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
- **Store** (`/data/problems` in containers, `.devdata/problems` in dev): `<slug>/v<NNN>-<hash8>/`.
  Versions never change after they are written, so a rejudge always sees the files the submission
  was judged on, and the worker can cache by version. The public web role mounts the store
  read-only; only web-admin writes to it.

## Code layout

| Path | Contents |
|---|---|
| `pyproject.toml`, `uv.lock` | uv workspace root: ties the three Python packages together; one lockfile; ruff, mypy and pytest settings |
| `web/` | Django project: `config/` (settings, the two URL configurations), `accounts/`, `core/`, `tests/`, `Dockerfile` |
| `judge/worker/` | the worker package, its `Dockerfile`, `isolate.conf` and entrypoint |
| `common/grader_common/` | code shared by web and worker: problem format, scoring, importer (`grader-import`) |
| `fixtures/` | small made-up problems for tests (native and CMS Italian format) |
| `deploy/dev/compose.dev.yml` | the development stack (db, web, web-admin, worker) |
| `init.sh` | creates dev secrets and starts the dev stack |
| `scripts/` | `check.sh` (lint, types, tests) and `smoke.sh` (is the running stack healthy?) |
| `.github/workflows/ci.yml` | CI: the same checks, plus Django deploy checks and a secret scan |
