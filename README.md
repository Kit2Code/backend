# grader

A self-hosted practice grader (Thai/English) for old competitive-programming contest problems.
People browse problems, read the statement, submit C, C++ or Python, and get per-test and
per-subtask results scored the same way [CMS](https://cms-dev.github.io/) v1.5 scored the original
contest. Submissions run inside the [isolate](https://github.com/ioi/isolate) sandbox.

It is written in Python only: Django 5.2 with server-rendered templates (plus htmx) for the web
site, and a Python judge worker that drives isolate. It runs as one Docker Compose app on a small
home server, next to an existing CMS installation.

> **Status:** early development. The repository skeleton, the dev stack and the CI checks exist;
> problems, judging and the admin pages are still being built.

## Quick start (development)

You need Linux (or WSL 2) with cgroup v2, Docker with Compose, and
[uv](https://docs.astral.sh/uv/). Then:

```sh
./init.sh              # create dev secrets, build the images, start db, web, web-admin and worker
./scripts/smoke.sh     # check that both web roles and the sandbox answer
./scripts/check.sh     # ruff, mypy and the tests
```

The public site is at <http://127.0.0.1:8000> and the admin site at <http://127.0.0.1:8001>.
Both listen on 127.0.0.1 only.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full set-up, how to run tests and the project rules.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the parts of the system and how they talk.
- [docs/DECISIONS.md](docs/DECISIONS.md): design choices, with the reason and the alternatives.
- [docs/PROBLEM_FORMAT.md](docs/PROBLEM_FORMAT.md): how to package a problem, and how CMS tasks are imported.

## Repository layout

```
web/            Django project (one image, two roles: public site and admin site)
judge/worker/   judge worker and its Docker image (isolate built from source)
common/         code shared by the web app and the worker: problem format, scoring, importer
fixtures/       small made-up problems used by the tests
deploy/dev/     the development Docker Compose stack
scripts/        check.sh, smoke.sh
docs/           documentation for contributors
```

## Licence

[GNU AGPL-3.0](LICENSE). If you run a modified version for other people, you must offer them the
source of your version.
