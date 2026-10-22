"""The production images: what they contain, who they run as, and what they start.

The images are built once per test session (Docker's cache makes repeat builds quick) under
local test tags; the release workflow builds the same Dockerfiles.
"""

import json
import shutil
import subprocess
from collections.abc import Callable

import pytest

from deploy.tests.conftest import REPO

DOCKER = shutil.which("docker") or "docker"
WEB = "grader-web:prod-test"
WORKER = "grader-worker:prod-test"


def build(tag: str, dockerfile: str, target: str | None = None) -> None:
    argv = [DOCKER, "build", "-q", "-f", str(REPO / dockerfile), "-t", tag]
    if target:
        argv += ["--target", target]
    result = subprocess.run(  # noqa: S603 - fixed argv list
        [*argv, str(REPO)], capture_output=True, text=True, timeout=1800, check=False
    )
    assert result.returncode == 0, result.stderr[-3000:]


@pytest.fixture(scope="session")
def images() -> None:
    build(WEB, "web/Dockerfile", target="prod")
    build(WORKER, "judge/worker/Dockerfile")


def in_image(image: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv list
            [DOCKER, "run", "--rm", "--network", "none", "--entrypoint", argv[0], image,
             *argv[1:]],
            capture_output=True, text=True, timeout=120, check=False,
        )  # fmt: skip

    return run


def config(image: str) -> dict[str, object]:
    out = subprocess.run(  # noqa: S603 - fixed argv list
        [DOCKER, "image", "inspect", image, "--format", "{{json .Config}}"],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    return dict(json.loads(out.stdout))


@pytest.mark.usefixtures("images")
def test_the_web_image_runs_as_a_plain_user() -> None:
    assert config(WEB)["User"] == "app"
    assert in_image(WEB)("id", "-u").stdout.strip() == "10001"


@pytest.mark.usefixtures("images")
def test_the_web_image_has_collected_static_files_and_compiled_translations() -> None:
    run = in_image(WEB)
    listing = run("find", "/app/web/staticfiles", "/app/web/locale", "-type", "f").stdout
    assert "/app/web/staticfiles/admin/css/base.css" in listing
    assert "/app/web/staticfiles/site.css" in listing
    assert "/app/web/staticfiles/vendor/htmx/htmx.min.js" in listing
    assert "/app/web/locale/th/LC_MESSAGES/django.mo" in listing
    assert "/app/web/locale/en/LC_MESSAGES/django.mo" in listing


@pytest.mark.usefixtures("images")
def test_the_web_image_serves_with_gunicorn_and_has_no_build_tools() -> None:
    run = in_image(WEB)
    assert run("gunicorn", "--version").returncode == 0
    for tool in ("uv", "msgfmt", "gcc"):
        assert run("sh", "-c", f"command -v {tool}").returncode != 0, tool
    assert config(WEB)["Cmd"] == ["serve"]  # docker-entrypoint.sh starts gunicorn for the role


@pytest.mark.usefixtures("images")
def test_the_worker_image_starts_the_judge_loop() -> None:
    assert config(WORKER)["Cmd"] == ["judge-worker"]
    found = in_image(WORKER)("sh", "-c", "command -v judge-worker judge-selftest isolate")
    assert found.returncode == 0
