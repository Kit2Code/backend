"""The production edge refuses to start with a Google upstream that isn't https.

The ZimaOS compose sets GRADER_REQUIRE_HTTPS_UPSTREAMS=1; the dev stack and the edge tests use a
plain-http stand-in for Google and leave it off.
"""

import shutil
import subprocess

import pytest

from deploy.tests.conftest import REPO
from deploy.tests.test_compose import services

DOCKER = shutil.which("docker") or "docker"
EDGE_IMAGE = "grader-edge:prod-test"
STRICT = "GRADER_REQUIRE_HTTPS_UPSTREAMS=1"


@pytest.fixture(scope="module", autouse=True)
def image() -> None:
    subprocess.run(  # noqa: S603 - fixed argv list
        [DOCKER, "build", "-q", "-f", str(REPO / "deploy/edge/Dockerfile"), "-t", EDGE_IMAGE,
         str(REPO)],
        capture_output=True, timeout=1800, check=True,
    )  # fmt: skip


def start(*env: str) -> subprocess.CompletedProcess[str]:
    """Run the edge's start-up checks, then `true` in place of Caddy."""
    env_args = [arg for value in env for arg in ("-e", value)]
    return subprocess.run(  # noqa: S603 - fixed argv list
        [DOCKER, "run", "--rm", "--network", "none", *env_args, EDGE_IMAGE, "true"],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip


@pytest.mark.parametrize("name", ["GRADER_GOOGLE_TOKEN_UPSTREAM", "GRADER_GOOGLE_CERTS_UPSTREAM"])
@pytest.mark.parametrize("value", ["http://evil.example:80", "evil.example:443", "HTTP://x"])
def test_a_plain_upstream_is_refused(name: str, value: str) -> None:
    result = start(STRICT, f"{name}={value}")
    assert result.returncode != 0
    assert name in result.stderr and "https" in result.stderr


@pytest.mark.parametrize(
    "env",
    [
        (STRICT,),  # the defaults (Google's https addresses)
        (STRICT, "GRADER_GOOGLE_TOKEN_UPSTREAM=https://oauth2.googleapis.com"),
        ("GRADER_GOOGLE_TOKEN_UPSTREAM=http://fake-oidc:8099",),  # dev: not strict
    ],
)
def test_https_or_not_strict_starts(env: tuple[str, ...]) -> None:
    result = start(*env)
    assert result.returncode == 0, result.stderr


def test_the_zimaos_edge_is_strict() -> None:
    assert services()["edge"]["environment"]["GRADER_REQUIRE_HTTPS_UPSTREAMS"] == "1"
