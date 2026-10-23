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
**Replaced** on 2026-10-21 by "HTTPS settings per role, and a clean deploy check".

## 2026-10-20: The problem format is pydantic models with a generated JSON Schema

**Choice:** `common/grader_common/problem_format.py` defines `problem.yaml` as frozen pydantic models
that refuse unknown keys. `python -m grader_common.problem_format` writes
`common/grader_common/schema/problem.schema.json`, and a test fails if the committed schema is out of
date.
**Why:** one definition shared by the importer, the web app and the worker, so they can't disagree.
Unknown keys are errors so that a typo (`time_limt_ms`) can't silently fall back to a default.
**Alternatives:** a hand-written JSON Schema plus separate parsing code (two definitions to keep in
sync).

## 2026-10-20: `io` field for file input/output

**Choice:** `problem.yaml` has `io: {input, output}`, file names a program reads and writes. Empty
(the default) means standard input / standard output.
**Why:** CMS's Italian-format loader (v1.5.1, `cmscontrib/loaders/italy_yaml.py`) defaults to
`input.txt` / `output.txt`, so old tasks may read and write files instead of stdin/stdout. Without
this field they couldn't be judged the way they were in the contest.

## 2026-10-20: `expected_score` is optional on solutions

**Choice:** a solution may leave out `expected_score`. Validation then runs it and reports the score
but doesn't require a particular result.
**Why:** imported CMS tasks often keep partial or slow solutions next to the real one in `sol/`, with
no record of what they should score. Requiring a number would either block publishing or force a
guess.

## 2026-10-20: Names and paths in `problem.yaml` are restricted

**Choice:** test names match `[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}` (the documented character set, but
not starting with a dot); every path is relative, made of plain names, and must be inside its own
folder (`checker/`, `graders/`, `solutions/`).
**Why:** these values become file paths on the server. Refusing `..`, absolute paths and leading dots
in the model means no later code can be tricked into reading outside the problem folder, and keeping
each kind of file in its folder keeps the "never served" rules simple.

## 2026-10-20: Scoring follows CMS, but refuses inputs CMS would mis-handle

**Choice:** `common/grader_common/scoring.py` re-implements CMS v1.5.1 Sum and GroupMin
(`cms/grading/scoretypes/`), taking tests in lexicographic name order as CMS does. Where CMS has no
rule we chose: a test without an outcome counts as 0; an outcome outside [0, 1] (or NaN) is an error;
a subtask with no tests is an error; a test pattern that matches nothing is an error.
**Why:** CMS crashes on an empty GroupMin subtask (`min([])`) while judging. Refusing it at import
gives a clear message instead of a judge error later. Out-of-range outcomes can only come from a bug
or a broken checker, and silently clamping them would hide that.
**Alternatives:** clamping outcomes to [0, 1]; treating an empty subtask as worth 0.

## 2026-10-20: How uploads are unpacked

**Choice:** `common/grader_common/importer/archive.py` refuses absolute paths, `..`, backslashes, `:`,
control characters, duplicate names, symlinks, device files, FIFOs and encrypted entries; more than
10 000 files, more than 1 GB unpacked, or any file over 256 MB. Sizes are checked from the zip
headers before anything is written, and counted again while writing. Files are created with
`O_EXCL | O_NOFOLLOW` in a new folder that is deleted on any error. macOS junk (`__MACOSX/`,
`.DS_Store`) is skipped silently, and a single wrapping folder is unwrapped.
**Why:** an upload is untrusted input that turns into files on the server. Checking headers first
stops zip bombs cheaply; counting again catches headers that lie. Backslashes and `:` are refused
because Windows-made zips use them for paths and drives, and they're never needed in a problem.
Skipping macOS junk avoids confusing errors for uploads zipped on a Mac.
**Alternatives:** `ZipFile.extractall` (it sanitises names but follows no size limits and doesn't
refuse links or special files).

## 2026-10-21: Importing CMS Italian-format tasks

**Choice:** `common/grader_common/importer/italy.py` re-implements the task rules of CMS v1.5.1
`cmscontrib/loaders/italy_yaml.py` (names, limits, statements, `gen/GEN` subtasks, `n_input`,
`total_value`, score-type overrides, graders, checker, file I/O, public tests) and converts the task
to our native layout. Where our format differs:
- **Public tests are not samples.** CMS `public_testcases` only showed results to contestants; our
  samples are downloadable by anyone. They are listed as a warning, and an optional `samples:` key in
  task.yaml (same syntax) picks the samples.
- **The checker must be source** (`check/checker.cpp` or `cor/correttore.cpp`). CMS stores the
  compiled `check/checker`; a binary built for another machine may not run in our sandbox and
  can't be reviewed. If source is present we use it even without the binary.
- **Reference solutions come from `sol/`** (CMS ignores them): every C/C++/Python file except
  graders, stubs and headers. `soluzione.*` / `solution.*` must reach the full score; the others are
  only reported. A C++ solution is assumed to be C++17.
- **Statements:** only Thai and English are kept. If neither exists, the primary-language statement
  is stored as Thai, with a warning (CMS's default primary language is Italian, which old Thai
  contests often left unset).
- **Where CMS would crash** (failed `assert`, missing key, `min([])` on an empty subtask) the import
  stops with a clear message. `memory_limit` over 512 MiB is refused. `n_input: 2.5` is refused
  instead of becoming 2.
- **Not supported:** OutputOnly and Communication tasks, score types other than Sum and GroupMin,
  graders for languages we don't judge, and the deprecated `../<task>.yaml` location.
**Why:** old tasks must score as they did in the contest, and nothing should be published by
accident.
**Credit:** the language-name table (`LANGUAGE_MAP`) is copied from CMS v1.5.1
`cmscontrib/loaders/base_loader.py` (AGPL-3.0, same licence as this project).

## 2026-10-21: Worker container security profile

**What isolate 2.7 needs from its container:**
- **Root with `CAP_SYS_ADMIN`.** isolate is setuid root. Each box gets new mount, IPC, UTS, PID and
  network namespaces, bind mounts and a `chroot`, which all need `CAP_SYS_ADMIN`. Switching to the
  box user (uid 70000 and up) needs `CAP_SETUID`/`CAP_SETGID`, and preparing the box folder needs
  `CAP_CHOWN`/`CAP_FOWNER`/`CAP_DAC_OVERRIDE`. Docker gives these three to every container already.
- **A writable cgroup v2 tree in the host cgroup namespace.** isolate makes one cgroup per box under
  `/sys/fs/cgroup/grader-judge` and reads its memory and OOM counters. Docker mounts
  `/sys/fs/cgroup` read-only in a container that isn't privileged, so it has to be mounted again
  read-write.
- **Seccomp and AppArmor that allow `unshare`, `mount` and `chroot`.** Docker's default AppArmor
  profile blocks `mount` even when `CAP_SYS_ADMIN` is granted.

*(Updated 2026-10-22: dev and production now both use the narrow profile, with NET_ADMIN and without the cgroup bind mount; see "Worker profile outcome".)* **Dev, now (`deploy/dev/compose.dev.yml`):** the worker is `privileged: true` with `cgroup: host`
and `init: true`. It is only on `judge_net` (internal: no internet, no route to the database), has
no host ports, no Docker socket and no secrets (the worker token comes with the worker loop). The
problem store is mounted read-only, the judge cache read-write. The root filesystem is writable
(isolate keeps its boxes in `/var/local/lib/isolate`). The environment report from this setup is
in `docs/env-dev.json`: all 41 capabilities, cgroup v2 mounted read-write, seccomp in filter mode
(the dev host's Docker 29 still applies its seccomp filter to privileged containers) and no
AppArmor on the dev host.

**Choice for production:** a narrower profile than `privileged`: `cgroup: host`,
`cap_add: [SYS_ADMIN]`, `security_opt: [seccomp=unconfined, apparmor=unconfined]`, and a volume `/sys/fs/cgroup:/sys/fs/cgroup:rw`, without `privileged`. Plus, as in dev: the problem
store read-only, only the `worker_token` secret directory, `judge_net` only, no Docker socket. It is
adopted only if the whole sandbox suite (`uv run pytest -q judge/worker/tests`, including every
hostile fixture) passes with it. If a test fails only because of the profile, the worker falls back
to `privileged: true`, and the failing test is named in this entry. The environment report shows on
the admin System page which capabilities, seccomp mode and AppArmor label the worker really got.
**Why:** a privileged container sees every host device (`/dev/sda` and friends), has all
capabilities (loading kernel modules, raw I/O, changing the network), and its `/sys` and `/proc` are
not masked: root in it is close to root on the server, which holds family files. The narrower
profile keeps Docker's device restrictions and masked paths and drops about 26 capabilities, while
still giving isolate what it needs. It is still a strong profile (`CAP_SYS_ADMIN` with no seccomp
filter), so the other walls stay: no internet, no database, one secret.
**Alternatives:** `privileged: true` everywhere (simplest, how CMS v1.5 is usually run; much wider
than needed); also keeping Docker's default seccomp profile (it allows `unshare` and `mount` once
`CAP_SYS_ADMIN` is added, so it may work and would be narrower still; to try after the profile above
passes); a private cgroup namespace (box cgroups would live under Docker's cgroup for the container,
which is removed and recreated on every restart; the setup is designed around one fixed subtree).
**Tightening found in the sandbox audit (to apply in the same trial):** `cap_drop: [ALL]` and add back
only `SYS_ADMIN, SETUID, SETGID, CHOWN, DAC_OVERRIDE, FOWNER, SYS_CHROOT, KILL, MKNOD` (`MKNOD` for the
boxes' small `/dev`, see below; not `DAC_READ_SEARCH`);
`no-new-privileges:true` (isolate no longer has the setuid bit); a read-only root filesystem with tmpfs
for `/run` and `/tmp`; `mem_limit` and `pids_limit` for the worker process itself (boxes are in
`grader-judge`, outside that limit); then try Docker's default seccomp profile too. Known residual risk:
`CAP_SYS_ADMIN` without seccomp and a read-write `/sys/fs/cgroup` is still close to host root for anyone
who becomes root in the container (cgroup v1 `release_agent` class of escapes, writing other apps'
cgroups); longer term, a one-shot setup step could create `grader-judge` so the worker needs only that
subtree writable.
**Status (2026-10-21):** not tried yet. Dev still runs `privileged: true` until the suite has been run
under the narrower profile; the result ("adopted" or "fell back: <test>") goes here.

## 2026-10-21: Program output counts toward the memory limit

**Choice:** keep the program's output as a file inside the box, as CMS does. The file's page cache is
charged to the box's memory cgroup, so `cg-mem` grows with the output (about 1 MB per MB written). A
program that writes a flood of output is usually killed by the memory limit before it reaches the
64 MB output limit and gets **MLE**, not OLE, and a program whose memory plus output is above the
memory limit gets MLE too.
**Why:** this is exactly how CMS v1.5 judges (isolate `--cg-mem`, output file in the box), so verdicts
match the contests these problems come from, and the code stays simple. Real problems have small
outputs compared with their memory limits.
**Alternatives:** read the program's stdout through a pipe that the worker drains and counts (output
no longer costs memory and OLE is exact, but it differs from CMS and adds code that handles hostile
output in the worker); report OLE first whenever the output reached the size limit (helps only the
few floods that get that far).

## 2026-10-21: Temporary disk use by programs writing many files

**Choice (for now):** accept it in dev, and fix where boxes live before production. The file-size
limit (64 MB) applies per file and boxes live on disk without a quota, so a program can write many
files (roughly 1 GB per second of CPU time) or many empty files (inodes) until its time limit stops it.
Everything is deleted when the box is cleaned up after each step, and the hostile suite checks that
nothing is left.
**Limits of this choice:** the CPU limit can be up to 60 s × time scale, so this is more than "a few
GB" in the worst case; cleaning up millions of files takes time while the slot is held; and boxes are
in the container's own filesystem, which sits on Docker's data disk (on ZimaOS often the small system
disk, shared with every other app). **Before the production compose:** put `box_root` on its own
volume under `/DATA/AppData/grader/`, ideally a fixed-size filesystem image with quotas so isolate's
`--quota` works; add an inode-flood hostile test with a bound on cleanup time.
**Why:** the time limit bounds it in dev, and the box is always removed.
**Alternatives:** isolate's `--quota` (needs a filesystem with quotas; Docker's overlayfs and bind
mounts usually don't have them); boxes on a size-capped tmpfs (files would count toward the memory
limit, which reverses keeping boxes on disk).

## 2026-10-21: Public sign-in with Google only

**Choice:** students sign in on the public site with Google only (OpenID Connect authorization-code flow with
`state`, `nonce` and PKCE, using Authlib). We ask for the `openid` scope only and store just Google's account id
(`iss` + `sub`); on first sign-in the student picks a username, display name and optional school. Staff keep
username + password (Argon2), and only on the admin port. The Google client ID and secret are pasted into an admin
page and the secret is stored encrypted (Fernet, key derived from the Django secret key with HKDF).
**How web reaches Google without internet access:** the public web container stays on internal-only networks. The
edge gets an extra listener on the internal app network that forwards exactly two fixed paths to Google over
verified TLS (token endpoint and signing keys) and returns 404 for anything else. The browser part of sign-in is a
plain redirect, so no Google JavaScript runs on our pages and the CSP stays strict. Dev and tests use a small fake
OpenID Connect provider in the dev stack, because automated tests can't sign in to real Google.
**Why:** no public passwords to store, reset or brute-force, and no reset emails; asking for `openid` only keeps
email addresses out of the database and backups. The admin port is plain HTTP on the LAN, which Google doesn't
accept as a redirect target, so staff need passwords anyway. Forwarding through the edge keeps "the web roles have
no direct internet" true: an SSRF bug in the public site can reach only Google's token endpoint.
**Alternatives:** username + password for everyone (more to protect and support); Google plus passwords (both sets
of code); django-allauth (larger, its own models and templates to override); hand-written OIDC (more
security-sensitive code of our own); storing the email too (personal data in backups); giving the public web
container normal internet access (simpler, but an SSRF bug could reach anything); passing the client secret as an
environment variable (visible in `docker inspect`).

## 2026-10-21: Sandbox hardening after the first sandbox audit

**Choices:**
- **A small `/dev` in every box.** By default isolate shows the container's whole `/dev` in each box; in a
  privileged container that includes host devices such as `/dev/kmsg` (the host kernel log). The worker's setup
  now builds `/var/local/lib/grader-judge-dev` with only `null`, `zero`, `full`, `random`, `urandom` and an empty
  `shm` folder (isolate mounts each box's private tmpfs there), and every run passes
  `--dir=/dev=/var/local/lib/grader-judge-dev:dev`. Inside a box `/dev` is read-only.
- **Memory reservations that survive a restart are dropped.** Each entry stores its process id *and* the process
  start time, because container process ids repeat after a restart; setup also clears the ledger at start.
- **A box whose cleanup failed is never reused.** Its slot is marked broken (cleared at the next start); if every
  slot is broken, jobs fail fast with a judge error instead of waiting forever.
- **White-diff streams.** It runs in the worker, outside any box, so it must not load a hostile output into
  memory token by token (a 64 MB line of one-letter tokens used about 3 GB). It now reads both files in 64 KB
  pieces and compares them as streams, with the same result as CMS v1.5.1 (golden tests, plus a randomised
  comparison with a copy of CMS's own logic).
- **Everything a box produces is cleaned and capped before it leaves the worker:** all Unicode control and format
  characters (C0, C1, bidi overrides, zero-width) are removed except tab and newline; compile logs are cut at
  64 KB of UTF-8, other messages at 4096 characters. Any unexpected error while judging becomes a judge error
  (JE) for that submission instead of crashing the job.
- **Problem packages are checked harder at import:** no file names starting with `-`, `io.input`/`io.output`
  can't clash with the executable or the worker's own stream files, and a language time factor must be in
  (0, 10]. The memory budget (`JUDGE_MEM_TOTAL`) must be at least the largest step limit (512 MB).
- **Compiled checkers are cached safely.** Each is written to a unique temporary file, synced to disk and then
  renamed into place, with a `checker.sha256` file beside it. A cached checker is used only if it is a regular,
  non-empty ELF file matching its checksum; anything else is rebuilt, so a half-written file after a power cut
  can't turn every test of a problem into a judge error.
- **A ceiling on the whole judge cgroup:** `cpuset.cpus` = the judge CPUs and a `pids.max`, so even a box with a
  wrong per-box CPU setting can't run on the CPUs used by CMS or the web apps.
**Why:** each closes a concrete path found by the audit (reading host devices, a judge that stalls after a power
cut, false TLE/MLE from a leftover process, running outside the judge CPUs).
**Alternatives:** an `isolate` rule list without `/dev` at all (compilers and Python need `/dev/null` and
`/dev/urandom`); relying on the narrower container profile alone to hide devices (it isn't tried yet, and the
fallback is privileged).

## 2026-10-21: The dev stack has an edge in front

**Choice:** the dev stack runs stock Caddy as `edge` with `deploy/dev/edge/Caddyfile`: `:8000` →
web with `X-Edge-Zone: public`, `:8001` → web-admin with `admin`, and `:8090` for sign-in. The web
roles are no longer published on the host. *(Replaced 2026-10-22: dev now runs the production edge
image, see "One edge configuration for production, dev and tests".)*
**Why:** the zone check needs the header on every request; with the edge in front, dev exercises
the same layer as production, and the web roles have no route out, as in production.
**Alternatives:** a setting that lets requests without a zone through in dev (simpler, but dev
would then differ in exactly the layer under test).

## 2026-10-21: Google traffic through one locked-down edge listener

**Choice:** edge `:8090` forwards only `POST /token` and `GET /certs`, listens only on the edge's
`app_net` address and serves only the public web role's address (both fixed via a fixed `app_net`
subnet), and caps request bodies at 16 KB. The web side reads both responses with a 64 KB cap.
**Why:** web keeps no general internet access; web-admin, the worker and the host can't use the
listener at all. Caddy can't limit response sizes, so that limit lives in `accounts/google.py`.
**Alternatives:** listening on all of the edge's addresses (reachable from the host's bridge);
internet access for web.

## 2026-10-21: Sign-in code: Authlib for the requests, joserfc for the ID token

**Choice:** Authlib's `OAuth2Session` builds the sign-in URL (with PKCE) and makes the token
request; the ID token is verified with joserfc (the JOSE library Authlib itself depends on), with
the checks spelled out in `accounts/google.py`: RS256 only, issuer, audience, `azp`, expiry,
issue time, nonce. Both Google issuer spellings (`https://accounts.google.com` and
`accounts.google.com`) are accepted and stored as the first.
**Why:** each security check is visible and tested in one place; Authlib's own ID-token helpers
use the same library underneath. Google documents both issuer forms.
**Alternatives:** Authlib's Django integration (hides the checks, uses OpenID discovery, which
would need another outgoing URL); writing the OAuth requests by hand.

## 2026-10-21: Client address for rate limits and logs

**Choice:** public role: `Cf-Connecting-Ip` if it is a valid address; both roles: then the last
`X-Forwarded-For` entry (the one the edge writes); then the TCP peer.
**Why:** behind the edge, the TCP peer is always the edge, so it can't tell people apart. Only
Cloudflare reaches the public listener, and Cloudflare sets `Cf-Connecting-Ip` itself. Earlier
`X-Forwarded-For` entries come from the client and are ignored.
**Alternatives:** the TCP peer only, as first planned for the admin site (every admin would share
one address).

## 2026-10-21: Our own rate limiter in Postgres

**Choice:** `core/ratelimit.py`: one counter row per (limit, key, fixed window), bumped by a single
`INSERT … ON CONFLICT DO UPDATE … RETURNING count`. Keys are stored as SHA-256 hashes. Refused
attempts count too. Old rows are not cleaned up yet; a housekeeping job comes with the admin
system pages.
**Why:** exact across gunicorn processes, no Redis, and user input (usernames) never reaches the
table as text.
**Alternatives:** django-ratelimit with the cache (needs a shared cache); sliding windows (more
rows, little benefit at this size).

## 2026-10-21: Sessions: database, role mark, and "log out everywhere"

**Choice:** database sessions, 30 days. Each session carries the role it was made in;
`RoleSessionMiddleware` deletes sessions from the other role and sessions without a mark. "Log out
everywhere" raises `User.session_version`, which is part of the session hash Django checks on every
request.
**Why:** a stolen public cookie must never work on the admin site. The session hash already
invalidates sessions after a password change; adding a counter does the same for Google users, who
have no password, without scanning the session table.
**Alternatives:** deleting the user's session rows (needs a user index on sessions); signed-cookie
sessions (can't be revoked).

## 2026-10-21: Thai by default, whatever the browser asks for

**Choice:** `ThaiByDefaultMiddleware` removes `Accept-Language` before Django's `LocaleMiddleware`,
so the site is Thai until the visitor presses the English button (kept in a cookie).
**Why:** many Thai students' browsers are set to English; the site is meant for them in Thai.
**Alternatives:** follow the browser (Django's default).

## 2026-10-21: Translations are compiled, not committed

**Choice:** only the `.po` files are in git; `.mo` files are ignored and built by `init.sh`,
`scripts/check.sh`, CI and a stage of the web image. Developers need GNU gettext.
**Why:** one source of truth, and no stale binary files in reviews.
**Alternatives:** committing `.mo` files (no gettext needed, but they drift from the `.po` files).

## 2026-10-21: HTTPS settings per role, and a clean deploy check

**Choice:** public role: Secure session, CSRF and language cookies, HSTS for one year with
subdomains, and `SECURE_PROXY_SSL_HEADER` (the edge must set `X-Forwarded-Proto: https` on its
public listener). Silenced there: `W008` (the HTTP→HTTPS redirect is Cloudflare's job; the
container only gets plain HTTP from the edge) and `W021` (HSTS preload needs the whole domain on
the browsers' preload list). Admin role (plain HTTP on the LAN): `W004`, `W008`, `W012`, `W016`
silenced. CI runs `check --deploy --fail-level WARNING` for both roles.
**Why:** each warning is either fixed or silenced with its reason, so a new warning fails CI.
Browsers treat `127.0.0.1` as secure, so the dev stack works with Secure cookies over plain HTTP.
**Alternatives:** HTTPS on the admin site too (needs certificates on the LAN; it isn't reachable
from outside).

## 2026-10-21: `import_problem --visible` skips validation, for dev and tests only

**Choice:** the `import_problem` management command can publish a problem at once.
**Why:** the problem pages and end-to-end tests need visible problems before the admin import page
(with validate-then-publish) exists. In production, problems are published from the admin site.

## 2026-10-21: Problem files: allow-list first, then strict path checks

**Choice:** a requested name must be on the version's allow-list (statement PDFs, `attachments/`,
sample tests, downloadable grader files). Then the path must have no `.`/`..` parts, resolve to
itself (no symlink anywhere), be opened with `O_NOFOLLOW`, and be a regular file.
**Why:** the store should never contain links (the importer refuses them), but the view must not
depend on that. Checking only that the resolved path stays inside the version folder would let a
symlinked folder point at `solutions/`.

## 2026-10-21: Tags are free text

**Choice:** `Tag.name` is a text field of up to 64 characters, not a slug.
**Why:** `problem.yaml` tags are free text and may be Thai.

## 2026-10-21: htmx and KaTeX later

**Choice:** neither is vendored yet. htmx comes with the submission results page; KaTeX when
statements need math rendering.
**Why:** nothing in the current pages uses them.

## 2026-10-22: Submit limits: cooldown and waiting count from rows, hourly count in the limiter

**Choice:** a submit locks the user's row (`select_for_update`), then checks: no submission of
theirs in the last 10 s, fewer than 3 of theirs queued or judging (both read from `Submission`),
and the hourly count through `core/ratelimit.py` (fixed one-hour windows). Only a valid form gets
this far, so a typo doesn't use up the hour. Requests over 256 KB are refused (413) before Django
reads the body; an uploaded file wins over the text box.
**Why:** the limiter's fixed windows suit an hourly cap but not a cooldown (two submits 0.2 s apart
could fall into two 10-second windows); the rows give an exact cooldown for free. The row lock
makes two submits sent at the same moment count one after the other.
**Alternatives:** everything in the limiter (inexact cooldown); everything from rows (would mean a
second rate-limit mechanism for the hourly cap).

## 2026-10-22: Live results by polling a fragment with htmx

**Choice:** htmx 2.0.4 is vendored (replacing "htmx later" above; KaTeX is still later). While a
submission is queued or judging, its results section carries `hx-get` + `hx-trigger="every 2s"`
and htmx swaps in a fresh copy from `/submissions/<id>/results`; the finished copy has no `hx-*`
attributes, so polling stops by itself. Pages and fragment are `Cache-Control: no-store`.
Per-test times are shown rounded to 10 ms and memory to 0.1 MB; the compile log only for CE,
and only to the submission's author, because compiler messages quote source lines.
**Why:** no WebSockets or extra services; a 2 s poll costs one small query per open page. Finer
time and memory figures are noise, and exact figures could help someone probe the machine.
**Alternatives:** server-sent events (needs a long-lived connection per viewer through Cloudflare
and gunicorn workers); a full page reload (loses the reader's scroll position).

## 2026-10-22: Ranking and "solved"

**Choice:** the ranking sums `UserProblemBest.best_score` over visible problems per active user in
one grouped query (zero totals left out), ordered by total, then by the latest `reached_at` among
the user's bests (whoever reached their total first wins), then by username. "Solved" on a profile
means the best score equals the current version's maximum from `problem.yaml` (cached per version).
**Why:** the covering index `upbest_rank_idx` serves the grouped query; the maximum score lives in
the problem files, and a profile has few rows, so reading it there is cheap. Hidden problems and
disabled users never show.
**Alternatives:** storing a `max_score` column on the version (a migration for one number we can
read from the cached format); dense ranks with shared places (the time tie-break makes them rare).

## 2026-10-22: The internal judge API trusts the worker's token, not its numbers

**Choice:** messages refuse NaN and Infinity (`allow_inf_nan=False`); scores, points, times and
memory must be ≥ 0, below fixed caps (1 hour, a 32-bit memory column), and a score may not exceed
its maximum (with 1e-6 slack for float sums). Anything else is 400 and nothing is stored. The claim
builds its reply inside the same transaction as the lease, so if the reply can't be built (for
example a `time_scale` outside (0, 10]) the claim rolls back: the job stays queued and keeps its
tries. The worker's HTTP client never follows a redirect.
**Why:** a single bad value (NaN never compares greater) would freeze a user's best score and the
ranking; a bad setting would otherwise use up every queued job's tries and turn them into judge
errors; a followed redirect would re-send the bearer token to the host in the `Location` header.
**Alternatives:** clamping bad numbers (hides a broken worker); checking `max_score` against the
problem files (left for later; the worker reads the same files).

## 2026-10-22: First-run setup link

**Choice:** while no superuser exists, each start of web-admin deletes unused setup tokens and makes
a new one (`secrets.token_urlsafe(32)`); only its SHA-256 is stored, it expires after 24 hours, and
the link is printed to the log and written (mode 0600, via a fresh temporary file and a rename) to
`/data/uploads/SETUP-LINK.txt`. `/setup?token=…` moves the token into the session and redirects to
plain `/setup`. Creating the superuser locks the token row and checks again that none exists. The
form is rate-limited per IP; anything unusable is a 404.
**Why:** no terminal on ZimaOS, so the link has to reach the owner through the app log or the Files
app; a stored hash means a database backup can't be used to take over a fresh install; the
redirect keeps the token out of the address bar and later request logs.
**Alternatives:** a default admin password (every install would share it until changed); an
environment variable with the first password (it would sit in the compose file).

## 2026-10-22: Publishing and validation

**Choice:** a problem becomes visible only through "publish" (the Import page or the Django admin
action), and only when its current version's validation passed; visibility and the current version
are read-only in the Django admin form. A new version of a public problem doesn't replace the
current one until it is published. The same zip twice reuses the version (and its validation).
Uploads are limited to 1 GiB (the importer's own limit for everything unpacked), saved in a fresh
folder under `/data/uploads/tmp` and removed after the import. Validation runs each reference
solution as an ordinary submission in one slot: it must get its `expected_score` (±1e-6), or,
without one, compile and be judged without a judge error. No solutions at all is valid, with a
warning.
**Why:** a problem must stay hidden until every reference solution got its expected score; editing the flags
by hand would skip that check; students keep the working version while a new one is checked.
**Alternatives:** validating in the web request (minutes of judging in a web worker); a separate
"publish" permission (one more thing to set up for a one-admin site).

## 2026-10-22: Job kinds and the report call

**Choice:** one queue (`JudgeJob`) for judge, validate, calibrate and selftest jobs. Non-judge jobs
are answered with `POST /internal/judge/report`; its body is a discriminated union (by `kind`) and
must match the job's kind; the report is stored in `JudgeJob.result`, and only a validation report
changes a version's status. A queued job that names nothing to work on is given up at claim time.
While grading is paused, only self-test jobs are handed out. A rejudged result recomputes the best
score from all of the user's results on that problem.
**Why:** leases, retries and priorities already work for submissions; a malformed job would
otherwise make every claim fail and block the queue; the self-test checks the judge itself and is
most useful on contest days; a rejudge can lower a score.
**Alternatives:** separate tables per job kind (duplicated lease logic); one `complete` call with
optional fields (weaker validation).

## 2026-10-22: Self-test and calibration

**Choice:** the self-test judges every program in `fixtures/hostile` (copied into the worker image
at `/opt/grader/fixtures` with `aplusb` and an accepted solution, which run first and last) with
the problem's own limits (time scale 1.0) and accepts the verdicts the sandbox tests accept.
Calibration runs each reference solution 5 times with limits 10 times the contest's and keeps
each test's slowest CPU time; the suggested time scale is the largest 2 × time ÷ limit over the
tests the solution passed in every run (a brute force that hits TLE would otherwise push it to 10),
rounded up to 0.1, between 1.0 and 10. The admin applies it by hand.
**Why:** the self-test must mean the same as the sandbox tests, on the real host; calibrating with
larger limits measures slow runs instead of cutting them off; "a reference solution uses at most
half the limit" is the usual rule for setting limits.
**Alternatives:** running the pytest sandbox suite on the server (needs the dev stack and docker);
applying the suggestion automatically (a noisy run would change every limit).

## 2026-10-22: Audit log details

**Choice:** `AuditedModelAdmin` (the base of every model admin, checked by a test) writes an audit
row for every add, change and delete in the Django admin, with Django's stored change message
(language-neutral JSON, not the translated text). Our pages and actions call `audit.record()`.
Staff sign-in, failed sign-in (username only) and sign-out are recorded from Django's auth
signals, in the admin role only.
**Why:** every admin action must be traceable, Django admin edits included; the log must read the same
whatever language the admin uses.
**Alternatives:** a `post_save` signal on every model (no request, so no IP or actor).

## 2026-10-22: Backups page and admin access to the Google keys

**Choice:** web-admin mounts `/data/backups` read-only, lists only plain `*.dump` files and serves
them (to superusers only: a dump holds every session and password hash) with `O_NOFOLLOW` and
`O_NONBLOCK` (a FIFO can't hang the request); "back up now" adds a `BackupRun` request row (one at a time; the settings
row is locked as a mutex) for the backup service. On the edge's `:8090`, web-admin (a fixed
`app_net` address) may `GET /certs` and nothing else, so the Sign-in page's test button fetches
the keys exactly as a sign-in does. The production edge must keep the same rule.
**Why:** web-admin never runs `pg_dump` or holds the database superuser; Google's keys are public,
while `/token` stays reachable only by the public role.
**Alternatives:** a test button that only checks the saved values locally (it wouldn't show that
the egress works); letting web-admin use all of `:8090`.

## 2026-10-22: One edge configuration for production, dev and tests

**Choice:** `deploy/edge/Caddyfile` is baked into the `grader-edge` image and used everywhere. The
addresses it needs (web roles, :8090's bind address and allowed callers, the Google upstreams, the
forwarded protocol) are `{$NAME:production default}` placeholders: production uses the defaults, the
dev stack sets dev values (and the fake Google sign-in), and `deploy/tests/test_edge.py` runs it
against echo servers. The strict Content-Security-Policy (`default-src 'self'`, no inline scripts or
styles, no frames) is set there; the browser tests fail on any CSP violation they see. The edge's
own health is a listener on its loopback (`:8079`), not a request through to Django.
**Why:** one file means dev and the browser tests exercise exactly what runs on the NAS; a CSP
mistake shows up in dev, not after an install.
**Alternatives:** separate dev and production Caddyfiles (they drift); a CSP with `unsafe-inline`
(htmx only needed one setting to avoid it).

## 2026-10-22: Production layout on the NAS

**Choice:** CPU 3 for every service and CPUs 4,5 for the judge's two sandboxes (`JUDGE_BOX_CPUS`),
leaving 0–2 to CMS; memory limits from the resource table (about 2 GB, plus 1 GB for sandboxes).
`app_net` is `10.89.91.0/24` with fixed addresses for the edge, web and web-admin (the dev stack
keeps `10.89.90.0/24`). The worker's container hostname is fixed (`grader-worker`), so its id stays
the same across updates. The compose file in git holds placeholders for the public hostname, the
admin address and the tunnel token; the owner fills them in the ZimaOS app settings. Extra admin
addresses (Tailscale) go in `GRADER_ADMIN_EXTRA_URLS`. `secrets-init` also hands `uploads/` and
`problems/` to the web user (Docker creates missing bind-mount folders as root). `/healthz` is
answered by the first middleware, whatever the Host header, for Docker's healthchecks. Third-party
images are pinned by digest to releases at least a few weeks old (cloudflared 2026.9.2).
**Why:** the NAS has CPUs 0–5 only; CMS's workers keep their own cores; the dry run showed that the
web user couldn't write the setup link and that healthchecks were refused by `ALLOWED_HOSTS`.
**Alternatives:** the spec's first sketch (CPUs 6,7: they don't exist here); real values in git (the
owner asked to keep the hostname out).

## 2026-10-22: Production images and operations tools

**Choice:** `web/Dockerfile` has `dev` and `prod` stages; `prod` holds only Python, the virtualenv,
the code, collected static files and compiled translations, runs gunicorn (gthread; public 2 × 4,
admin 1 × 4 with a 15-minute timeout for imports, no control socket) as uid 10001. `grader-ops` is
the Postgres image of the same version plus Python, so `pg_dump`/`pg_restore` match the server.
`scripts/build-images.sh` is the one way to build the four images (CI builds them on every push,
the release workflow pushes them on a `v*` tag with the job's `GITHUB_TOKEN`).
**Why:** small images with nothing to build code with; one build path for CI, release and the dry run.
**Alternatives:** third-party build/push actions (more pinned dependencies for the same result).

## 2026-10-22: Worker profile outcome, and deadlines for isolate

**Choice:** the worker runs with `cap_add: [SYS_ADMIN, NET_ADMIN]`, seccomp and AppArmor unconfined
and `cgroup: host` — not `privileged` — in production *and* in the dev stack, so the whole sandbox
suite runs under the production profile. Two findings from the production dry run changed the
profile chosen on 2026-10-21: isolate needs NET_ADMIN to switch on the loopback interface in each
sandbox's network namespace ("SIOCSIFFLAGS on 'lo' failed: Operation not permitted" without it),
and bind-mounting the host's `/sys/fs/cgroup` into the container makes every sandbox start fail
("Proxy failed before it passed box_pid"), even in a privileged container. So there is no bind
mount: the worker's entrypoint re-mounts Docker's own (read-only) cgroup mount read-write, which
CAP_SYS_ADMIN allows. Every call to isolate now has a deadline (a run: its wall-time limit + 30 s;
`--init`/`--cleanup`: 60 s); a missed deadline is an IsolateError (a judge error for that job)
instead of a judging slot stuck forever — which is what the failing sandbox start had caused.
**Limits of this profile (sandbox audit, 2026-10-22):** it removes `privileged`'s extra devices
and capabilities, but root *inside the worker* (someone who already escaped isolate or the kernel)
can still reach the host: with `cgroup: host`, the cgroup tree read-write and CAP_SYS_ADMIN without
seccomp or AppArmor, it can leave the container's cgroup or re-mount `/proc/sys`. The walls that
really count are isolate and the kernel; the worker has no internet, no database and one secret,
so that is what such an escape starts from. Narrower options (a private cgroup namespace with a
delegated subtree, dropping MKNOD/NET_RAW, Docker's default seccomp profile) are listed as follow-ups.
**Why:** NET_ADMIN only affects the worker's own network namespace, which sits on the internal
`judge_net` with no route out; `privileged` would add every device and capability.
**Alternatives:** `privileged: true` (the planned fallback; not needed).

## 2026-10-22: Seccomp filter for sandboxed programs

**Choice:** every program in a box (compilers, submissions, checkers) starts through `box-exec`
(`judge/worker/launcher/box-exec.c`, built static into the worker image). It sets no-new-privileges,
installs a seccomp filter and then runs the program. The filter allows native x86-64 system calls
only (32-bit and x32 calls kill the process) and makes these fail with EPERM: io_uring, bpf,
userfaultfd, keyctl/add_key/request_key, perf_event_open, ptrace and process_vm_*, kernel modules
and kexec, unshare/setns/mount/umount2/pivot_root, open_by_handle_at, swap, reboot and acct; after
the audit also syslog (the host's kernel log), the new mount API (fsopen, fsmount, open_tree,
move_mount, …), pidfd_getfd, kcmp, process_madvise, move_pages, migrate_pages, memfd_secret,
vmsplice, personality, quotactl and fanotify_init. `clone` with any CLONE_NEW* flag gets EPERM, and
`clone3` gets ENOSYS (a filter can't read its flags; the C library then uses `clone`). If the
filter can't be installed the program isn't run. The worker container itself keeps Docker's default
seccomp profile (isolate works with it), instead of `seccomp=unconfined`.
**Why:** these interfaces are the usual way from a local program to kernel exploits. Docker's
default profile alone still allowed `add_key` and `perf_event_open` from a box (the hostile
`seccomp_surface.c` showed it), and a host may run Docker without it; a filter inside the box doesn't
depend on the host.
**Alternatives:** a custom Docker seccomp profile file (needs a file on the host, which the ZimaOS
install can't have); libseccomp (a library dependency for a 30-rule list).

## 2026-10-22: Disk limits per box

**Choice:** while a program runs in a box, the worker checks the box every 100 ms
(`judge/worker/src/grader_worker/diskwatch.py`). It stops the run (`cgroup.kill` on the box's
cgroup) and gives OLE when the box's files take more than `JUDGE_BOX_DISK_MB` (default 256) of disk
space, or when its processes have written more than `JUDGE_BOX_WRITE_MB` (default 512) to disk
during the run (the box cgroup's `io.stat`; the worker enables the io controller when the host
offers it). Both settings must be at least twice the output limit (128 MB). A box the watch can't check is
stopped too: a folder nested more than 2048 characters deep (or unreadable), more than 20,000 files
and folders, or any error inside the watch. A file deleted while still open isn't in any folder; its
data counts as written, and the box's memory and time limits bound it.
**Why:** isolate limits each file but not a box's total, so a program could fill the NAS's disk
with many files, or wear it out by writing and deleting over and over. Files stay on the disk, so
a program's memory is still counted the way CMS counts it.
**Alternatives:** the box folder on a size-limited tmpfs (simple, but the files would count as the
program's memory, unlike CMS); disk quotas (need quota support on the host's filesystem and a host
setting, which the ZimaOS install can't make).

## 2026-10-22: Client addresses only from the edge

**Choice:** the web roles use `Cf-Connecting-Ip` and `X-Forwarded-For` only when the request comes
straight from the edge (`GRADER_EDGE_ADDRESSES`, the edge's `app_net` address); otherwise the TCP
peer's address counts. Empty means never trust those headers.
**Why:** the rate limits and the audit log use this address. The worker can reach web-admin directly
on `judge_net`, and anything else that can reach a web role could otherwise pick any address for
itself.
**Alternatives:** trusting the headers from any private address (too wide: every container is on a
private address).

## 2026-10-22: Only https to Google from the production edge

**Choice:** the edge image checks at start that the Google token and key upstreams begin with
`https://` when `GRADER_REQUIRE_HTTPS_UPSTREAMS=1`, which the ZimaOS compose sets; it refuses to
start otherwise.
**Why:** the token request carries the app's Google client secret and the keys decide which sign-ins
are trusted; a plain-http upstream would let anyone on the path read or change them. The dev stack
and the edge tests use a plain-http stand-in for Google, so the check is a setting and not always on.

## 2026-10-22: Worker healthcheck

**Choice:** the judge loop touches `/run/grader-judge/alive` every time round; `judge-health` (the
container's healthcheck) fails when that file is missing or older than 90 s, or when every slot is
marked broken.
**Why:** the old check (does the cgroup folder exist) stayed green while the loop hung or no slot
could judge, so ZimaOS showed a healthy app that judged nothing.

## 2026-10-22: A plain database role for the web site

**Choice:** both web roles log in as `grader_app`, a role that isn't a superuser and can't make
databases or roles. It owns the database and the tables, so web-admin's migrations work. A one-shot
`db-init` (ops image) makes the role at every start, sets its password from the secrets, and hands
it anything the superuser still owns. The superuser `grader` is used only by the database itself,
`db-init` and the backups. The web secrets folder holds only `db_app_password`. Dev and CI keep
the superuser (`GRADER_DB_USER` unset), because the tests create their own database.
**Why:** a Postgres superuser can run shell commands in the db container (`COPY ... TO PROGRAM`) and
read the server's files, so a bug in the web site (an SQL injection) would reach much further.
**Alternatives:** two roles, a table owner for web-admin and a rows-only role for the public site
(stricter, but needs a separate secret folder for web-admin; possible later).

## 2026-10-22: Release safety

**Choice:** the release workflow first runs the same checks as CI (a copy of CI's check job, kept
equal by a test, read-only permissions) and pushes only if they pass. After pushing, it prints
`ghcr.io/kit2code/grader-*:<version>@sha256:…` for each image in the run's summary
(`scripts/build-images.sh --digests`), and the ZimaOS compose pins those digests. No checkout keeps
the job token on disk (`persist-credentials: false`).
**Why:** a tag on a broken commit would otherwise publish broken images; a version tag can be
pushed again, but a digest always names the same bytes, so the NAS installs exactly what was tested.
**Alternatives:** calling CI as a reusable workflow (less copying, but the workflow tests read each
job's steps, which a reusable call doesn't have).

## 2026-10-22: Worker profile

**Choice:** the worker container runs with `cgroup: private` (its own cgroup namespace) instead of
`cgroup: host`. Its entrypoint moves the worker into a leaf cgroup `worker` and makes
`grader-judge` (isolate's cgroup root) next to it, inside the container's own cgroup. It drops every
capability and adds back only `SYS_ADMIN`, `NET_ADMIN`, `SETUID`, `SETGID`, `CHOWN`,
`DAC_OVERRIDE`, `FOWNER` and `SYS_CHROOT` (each one tested as needed; `KILL`, `MKNOD` and
`NET_RAW` are not), with `no-new-privileges`, `pids_limit: 512` and Docker's default seccomp filter.
The boxes' `/dev` is made of bind mounts of the worker's own `null`, `zero`, `full`, `random` and
`urandom` instead of `mknod`. The container's memory limit is `JUDGE_MEM_TOTAL` + 384 MB.
**Why:** this replaces "Worker profile outcome". Root inside the worker (after an escape from
isolate) could write to the host's whole cgroup tree with `cgroup: host`, for example to lift
another container's limits or move itself out of its own. In a private namespace it sees and can
change only its own subtree, which Docker already limits. Every capability left out is one less tool
after an escape.
**Alternatives:** `cgroup: host` with a bind mount of a delegated subtree (breaks isolate);
`privileged` (everything allowed).

## 2026-10-22: Known remaining risk: /proc/sys from root in the worker

**What:** someone who already has root *inside the worker container* (after escaping isolate or
the kernel) can unmount Docker's read-only cover over `/proc/sys` in a new mount namespace and
write host-wide kernel settings such as `kernel/core_pattern`, which leads to root on the NAS. The
worker needs `CAP_SYS_ADMIN` and no AppArmor profile for isolate, and that is what allows it.
**Why not fixed yet:** the fixes need either a host-side setting (an AppArmor profile loaded on the
host, or Docker's user-namespace remapping, which changes every container on the host) or running
isolate inside a user namespace of the worker's own, which locks those mounts but has to be tried
with isolate's cgroups and sandbox users first. The layers in front of it stay: isolate, the box
seccomp filter, no capabilities in the box, and a worker with no internet, no database and one
secret.
**Next:** a follow-up step tries the user-namespace approach.

## 2026-10-23: Pico + one stylesheet

**Choice:** the new dark look keeps Pico (classless) as the base and puts everything else in one
hand-written `site.css`: Pico's colour variables set to a navy palette on `:root[data-theme="dark"]`,
plus a few small classes. Dark only. Icons are Lucide SVG files copied into
`core/static/vendor/lucide/` and inlined by the `{% icon %}` template tag. Drop-down menus are
`<details>` elements.
**Why:** no Node.js and no build step; pages load nothing from other sites; the CSP allows no inline
scripts or styles, which `<details>` and classes respect; inlined SVGs follow the text colour.
One theme halves what has to be checked on every page.
**Alternatives:** Tailwind or Bootstrap (needs a build step or a large file, and restyles everything
Pico gives for free); an icon font (blurry, harder to keep small); `<img>` icons (cannot take the
text colour); a light/dark switch (twice the checking for little gain).

## 2026-10-23: Max score and limits are copied onto the problem

**Choice:** `Problem` gets `max_score`, `time_limit_ms` and `memory_limit_mb`, copied from the
current version's `problem.yaml` by `registry.make_current` (where titles and tags are already
copied). Migration `problems/0003` fills them for existing problems from the store, and leaves NULL
for a problem whose folder is missing; such a problem counts as never solved on the Tasks page until
it is imported or published again. "Solved" means a best score of at least `max_score` (less a
1e-9 allowance for float sums).
**Why:** the Tasks page shows 50 problems, their limits, how many people solved each and the
user's own status. With these columns it is a fixed handful of queries and reads no files, so a
missing or slow store can't break or slow the list. The store's format cache holds only 256 files,
and the site will have more problems than that.
**Alternatives:** reading `problem.yaml` for each row (file reads on every page view, and an error
page if one folder is missing); putting them on `ProblemVersion` (also correct, but the list would
need a join for every row, and titles already live on `Problem`).

## 2026-10-23: Google profile photos, kept in the database

**Choice:** sign-in asks Google for `openid profile` (was `openid` only) to get the `picture`
claim; the name in the same token is ignored. The web fetches the photo once per sign-in through a
new edge route (`:8090/photo/<path>=s256-c`, to `lh3.googleusercontent.com` only), checks size and
file signature, and stores the bytes in an `Avatar` table (one row per user). `/users/<name>/photo`
serves it, with `?v=<time>` in page links so browsers may cache it. Users can switch photos off in
`/settings`, which deletes the stored copy at once.
**Why:** a photo makes the ranking and profile friendlier. Fetching through the edge and serving it
ourselves keeps visitors' browsers off Google (privacy, and the CSP stays `img-src 'self'`). The
database rather than files: the public web role has no writable volume and should not get one
(it is the internet-facing container); photos are small (Google's 256 px JPEGs are ~10-30 KB);
they are in the nightly backups; deleting a user deletes the photo.
**Alternatives:** linking Google's image directly (every visitor's browser would call Google, and
the CSP would need an outside image host); a writable avatars volume for the public role (more
attack surface, a compose change for the ZimaOS install); asking web-admin to store it (a
public-to-admin call path the role split avoids); resizing with Pillow (a new dependency; Google
resizes for us with the `=s256-c` suffix).

## 2026-10-23: The browser problem editor goes through the importer

**Choice:** the admin's New problem / Edit pages build a native problem folder from the form and
import it with `import_problem()`, exactly like an uploaded zip, then register it and queue
validation. Subtasks are typed one per line ("30 1-*"), and there are three fixed slots for
reference solutions. Editing copies the current version's folder first. The admin role accepts up
to 2,100 files per request (Django's default is 100) so a problem's tests can be picked at once;
the public role keeps 100.
**Why:** one path into the store means one set of safety checks, one versioning scheme and one
validation rule; the editor can't create a problem the importer would refuse. Plain text lines and
fixed slots need no JavaScript and no formsets, and cover what the problem format allows.
**Alternatives:** writing `Problem`/`ProblemVersion` rows and files directly (a second, divergent
path into the store); formsets for subtasks and solutions (more markup and JavaScript for little
gain).

## 2026-10-23: "Submissions" stays in the public nav

**Choice:** the new header keeps a "Submissions" link for everyone, next to Home, Tasks, Archive and
Ranking; the account menu has the user's own submissions.
**Why:** all submissions are public (without code) and a signed-out visitor should reach them; the
existing navigation test asks for the link on the home page.
**Alternatives:** only inside the account menu (signed-out visitors couldn't find the list).

## 2026-10-23: The admin site through Cloudflare Access, checked by the site too

**Choice:** besides the LAN and Tailscale, the admin site can be opened at its own hostname
through the existing Cloudflare tunnel, behind a Cloudflare Access policy. A separate edge listener
(`:8082`, zone `admin-remote`) carries this traffic, and web-admin verifies the Access token on
every request itself (RS256, the team's keys through edge `:8090`, issuer, audience, time),
answering 404 otherwise. Off until the owner fills in three settings.
**Why:** the owner wants to reach the admin without Tailscale. Access alone would make the site's
safety depend on one dashboard setting: a deleted or loosened policy would put the admin login on
the internet. Checking the token in the site makes that failure close the door instead. A separate
zone keeps LAN/Tailscale access exactly as it was, with no token needed there.
**Alternatives:** Tailscale only (no new exposure, but the owner asked for this); Access only
(simpler, fails open); checking the token in the edge (Caddy has no built-in JWT check; a plugin
would mean a custom Caddy build).

## 2026-10-23: "School" is hidden, not deleted

**Choice:** school is no longer asked for or shown anywhere; the `User.school` column stays.
**Why:** the owner doesn't want it; keeping the column needs no data migration and can be undone.
**Alternatives:** dropping the column (deletes what users typed; can't be undone without a restore).

