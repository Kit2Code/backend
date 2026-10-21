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

**Dev, now (`deploy/dev/compose.dev.yml`):** the worker is `privileged: true` with `cgroup: host`
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
