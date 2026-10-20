# Decisions

Design choices that aren't obvious from the code, newest at the bottom. Each entry gives the
choice, why, and what else was considered.

## 2026-10-20: Python only, Django with server-rendered pages

**Choice:** Django 5.2 LTS with templates and htmx for the web app, and a Python judge worker. No
Node.js and no JavaScript framework.
**Why:** one language for everything, less memory on a small shared server, and fewer moving parts.
**Alternatives:** a React/Vue frontend with an API backend (needs a Node build step and more RAM).

## 2026-10-20: One uv workspace, one lockfile

**Choice:** the repo is a [uv](https://docs.astral.sh/uv/) workspace with three members: `web`,
`judge/worker` and `common`. Python 3.12 only.
**Why:** the web app and the worker share code (`common`) and must agree on versions; one lockfile
makes builds repeatable.
**Alternatives:** separate repos or separate lockfiles (shared code would drift).

## 2026-10-20: One web image, two roles

**Choice:** the same Django image runs as the public site (`GRADER_ROLE=public`) and the admin site
(`GRADER_ROLE=admin`), each with its own URL configuration.
**Why:** the public container doesn't contain admin or internal URLs at all, so a public-site bug
can't expose them. One image keeps building and updating simple.
**Alternatives:** one container serving everything with permission checks (a single mistake exposes
the admin); two separate codebases (duplicate work).

## 2026-10-20: Custom user model from the first migration

**Choice:** `accounts.User`, a subclass of Django's `AbstractUser`, is the user model from
migration `0001`.
**Why:** Django can't switch to a custom user model after the first migration without a painful
manual data migration. Starting with our own empty subclass costs nothing.

## 2026-10-20: Secrets as files in per-service directories

**Choice:** secrets are read from files in a directory (`GRADER_SECRETS_DIR`), one directory per
service, mounted read-only. Never environment variables.
**Why:** environment variables leak easily (process listings, error reports, `docker inspect`).
Mounting a directory instead of a single file avoids Docker creating an empty directory in place of
a missing file on first boot.
**Alternatives:** env vars or Docker Compose `secrets:` (which mounts single files).

## 2026-10-20: Only web-admin runs migrations

**Choice:** web-admin runs `migrate` at start-up; the public web container waits until
`migrate --check` passes.
**Why:** two containers migrating at once can break a migration run. The entrypoints retry instead
of relying on Compose start order, which the target server's app importer may not keep.

## 2026-10-20: `DEBUG = False` everywhere, including development

**Choice:** `DEBUG` is hard-coded to `False`, with no setting to turn it on.
**Why:** Django's debug pages show settings and source code. One forgotten flag in production would
leak them, so the switch doesn't exist.
**Alternatives:** an environment toggle for dev (easy to leave on by mistake).

## 2026-10-20: Our own isolate namespace, isolate built from a pinned tag

**Choice:** isolate 2.7 is built from the official `ioi/isolate` tag, and the build fails unless the
tag points at the expected commit. Our config uses cgroup root `/sys/fs/cgroup/grader-judge`,
user/group ids from 70000 and box ids 100–109.
**Why:** the server also runs CMS with its own isolate (cgroup root `/sys/fs/cgroup`, ids from
60000, boxes from 0). Two isolates with default settings collide on cgroup names and user ids.
Pinning the commit means a moved tag can't change what we build. Version 2.6 or newer is needed:
2.6 stopped sharing `/dev/shm` between boxes and turned io_uring off by default.
**Alternatives:** a distribution package (old versions, can't set our own paths at build time).

## 2026-10-20: Dev stack differences from production

**Choice:** the dev compose file mirrors production's services and networks, with these
differences: an extra `dev_ports` network publishes Postgres and both web roles on `127.0.0.1`
only; Postgres data is a named volume; Django runs `runserver` with code reload; and the worker is
`privileged` for now.
**Why:** developers need to reach the database and sites from their machine, but nothing should
listen on the LAN. Production listens on no host ports except the edge. A narrower worker profile
is to be worked out together with the sandbox.
**Alternatives:** publishing ports on all interfaces (exposes the dev database to the LAN).

## 2026-10-20: Dev secrets are 64 characters

**Choice:** `./init.sh` generates secrets from 48 random bytes (64 URL-safe characters).
**Why:** Django's deploy check (`security.W009`) wants a `SECRET_KEY` of at least 50 characters;
32 bytes gave only 43.

## 2026-10-20: Licence AGPL-3.0

**Choice:** the code is licensed under the GNU AGPL-3.0.
**Why:** this is network software; the AGPL makes anyone who runs a modified version for others
share their changes. It also lets us learn from DMOJ and programming.in.th, which use the same
licence.

## 2026-10-20: CI on GitHub-hosted runners with pinned actions

**Choice:** CI runs on GitHub-hosted `ubuntu-24.04` runners only. Third-party actions are pinned to
commit SHAs and container images to digests (the version is in a comment). The secret scan runs
the official gitleaks container image over the whole git history.
**Why:** this is a public repository: a self-hosted runner would let anyone's pull request run code
on our machine, and a moved tag on an action could inject code into CI. The gitleaks GitHub Action
needs a registered licence key (a repository secret) for organisation-owned repositories; the
container image needs nothing.
**Alternatives:** `gitleaks/gitleaks-action` (licence key to manage), action version tags (mutable).

## 2026-10-20: HTTPS-related deploy-check warnings wait for the edge

**Choice:** CI runs `manage.py check --deploy` for both roles and fails on errors. The warnings
about HSTS, SSL redirect and secure cookies (`security.W004`, `W008`, `W012`, `W016`) are left for
now.
**Why:** those settings depend on how each role is reached: the public site over HTTPS through
Cloudflare and the edge, the admin site over plain HTTP on the home LAN. They will be set per role
when the edge is built, and the check tightened then. Setting them now would break the admin site.
