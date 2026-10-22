# Contributing

## Set-up

You need:

- Linux or WSL 2 with **cgroup v2** (isolate needs it). Check with
  `stat -fc %T /sys/fs/cgroup` → `cgroup2fs`.
- Docker with the Compose plugin.
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and every dependency for you).
- [gitleaks](https://github.com/gitleaks/gitleaks) 8.19 or newer and [pre-commit](https://pre-commit.com/).
- GNU gettext, to compile the translations: `sudo apt-get install gettext`.
- The system libraries for the browser tests' Chromium, once per machine (after the first
  `./init.sh` has installed the Python packages): `sudo uv run playwright install-deps chromium`.

Then, once per clone:

```sh
pre-commit install     # scan every commit for secrets
./init.sh              # dev secrets in .devdata/, build and start the stack
```

`./init.sh` is safe to run again at any time; it only creates what is missing and then rebuilds
and restarts the containers. It also downloads Playwright's Chromium, compiles the translations,
and sets up the dev stack's fake Google sign-in.

To see a problem on the dev site, import one:
`uv run python web/manage.py import_problem fixtures/problems/aplusb --visible`. To sign in, use
"Sign in with Google" on <http://127.0.0.1:8000>: in dev it opens a fake sign-in page
(`127.0.0.1:8099`) where you pick a test account. Then open the problem and press "Submit": the
dev worker (`judge-worker`) picks the job up within a couple of seconds, and the submission page
updates as each test finishes. `docker compose -f deploy/dev/compose.dev.yml logs -f worker` shows
what the worker is doing.

The admin site is <http://127.0.0.1:8001/admin/>. The first time, create your admin account with
the one-time link in `.devdata/uploads/SETUP-LINK.txt` (also printed in the `web-admin` log); it
works only while no superuser exists. From the admin home page, the **Grader** box leads to Import,
Grading, System, Calibrate, Backups and Sign-in. (The browser tests in `test_m5_admin.py` delete
the dev superusers and make a new link, so after running them use the new link again.)

## Day-to-day commands

| What | Command |
|---|---|
| Start or update the dev stack | `./init.sh` |
| Is the stack healthy? | `./scripts/smoke.sh` |
| All code checks (same as CI) | `./scripts/check.sh` |
| Tests only | `uv run pytest -q` (needs the dev stack: some tests drive its containers and a browser) |
| Browser tests only | `uv run pytest -q web/tests/e2e` (screenshots in `test-results/e2e/`) |
| New or changed text in the UI | `cd web && uv run python manage.py makemessages -l th -l en --ignore "static/*" --ignore "tests/*"`, translate in `web/locale/*/LC_MESSAGES/django.po`, then `uv run python manage.py compilemessages` |
| Lint and types | `uv run ruff check . && uv run ruff format --check . && uv run mypy .` |
| Django management | `uv run python web/manage.py <command>` |
| Import a problem into the store only | `uv run grader-import <zip or folder> --store .devdata/problems` |
| Import and publish on the dev site | `uv run python web/manage.py import_problem <zip or folder> --visible` |
| A new first-run setup link (no superuser yet) | `uv run python web/manage.py setup_link` |
| Run the sandbox self-test by hand | `docker compose -f deploy/dev/compose.dev.yml exec worker judge-selftest` |
| New migration | `uv run python web/manage.py makemigrations`, then commit the file |
| Secret scan | `gitleaks dir . -v` |

Running `manage.py` or `pytest` on your machine talks to the dev stack's Postgres on
`127.0.0.1:5432`, so start the stack first. Settings for that come from `.devdata/` automatically.

## Rules

These keep the server (a home NAS) and the public repository safe.

**Never commit secrets, real problems or personal data.**
No passwords, tokens or keys; no `.env` files (only `.env.example` with placeholders); no real
contest problems, tests or solutions; no submissions or user data; no real host names or IP
addresses. Tests use small made-up problems only. The pre-commit hook and CI run gitleaks.

**Untrusted code runs only inside isolate.**
That includes submissions, compilers and checkers. A sandbox always has no network, does not run
as root, and has limits on CPU time, wall time, memory, processes, file size and output.

**Never build shell commands from data.**
Call `subprocess` with a list of arguments, never `shell=True`, and never a string that contains
a file name, a language name or any other value that came from outside.

**Treat files from outside as hostile.**
Uploaded zips and anything unpacked from them can contain `../` paths, absolute paths, symlinks or
huge files. Check paths, refuse symlinks, and limit sizes.

**Keep the two web roles apart.**
The public role must never route admin, setup or internal URLs, not even by accident. Add a test
whenever you add a URL.

**Other rules**
- `DEBUG` stays `False` in every image and setting.
- Use the Django ORM. If raw SQL is ever needed, pass values as parameters, never with string
  formatting.
- No Docker socket mounts, and no changes to host-wide kernel settings from a container.
- Tests that exercise the sandbox, the database or the whole stack use the real thing (real
  isolate, real Postgres). Mocks are fine in small unit tests only.
- Never weaken or delete a test to make the build green. If a test looks wrong, raise it.
- Record every non-obvious design choice in [docs/DECISIONS.md](docs/DECISIONS.md), and keep
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) current when parts or connections change.

## Code from other projects

Code may be read for ideas from DMOJ and programming.in.th (both AGPL-3.0) and rusty-grader (MIT).
If you copy code, keep its licence notice and add an entry to `docs/DECISIONS.md`. Do not copy
code from projects without a licence (for example otog).

CMS behaviour (output comparison, task loading, compiler flags, score types) is re-implemented from
the CMS v1.5.1 source: cite the CMS file in a comment and add tests next to it. Do not depend on the
`cms` package.

## Style

Plain, readable, typed Python: clear names, type hints, short functions and docstrings on public
functions. Prefer standard Django features over extra libraries; ask before adding a dependency.
Ruff and mypy (strict) must pass.
