"""The release workflow and the image build script it uses.

The workflow runs on GitHub (the owner pushes a v* tag); these tests check its shape: when it runs,
what it may do, that every action is pinned, and that it builds through scripts/build-images.sh,
the same script CI and the production dry run use.
"""

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from deploy.tests.conftest import REPO

RELEASE = REPO / ".github" / "workflows" / "release.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"
BUILD = REPO / "scripts" / "build-images.sh"


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_the_release_runs_only_for_version_tags() -> None:
    workflow = load(RELEASE)
    # PyYAML reads the key `on` as True.
    trigger = workflow[True]
    assert trigger == {"push": {"tags": ["v*"]}}


def test_the_release_may_only_read_code_and_write_packages() -> None:
    assert load(RELEASE)["permissions"] == {"contents": "read", "packages": "write"}


def test_every_action_is_pinned_to_a_commit() -> None:
    for workflow in (load(RELEASE), load(CI)):
        for step in steps(workflow):
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_the_release_pushes_with_the_built_in_token_only() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    assert "scripts/build-images.sh" in text and "--push" in text
    used = set(re.findall(r"secrets\.(\w+)", text))
    assert used == {"GITHUB_TOKEN"}
    assert "--password-stdin" in text  # the token never appears on a command line


def test_ci_builds_the_images_without_pushing() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "scripts/build-images.sh" in text
    build_lines = [line for line in text.splitlines() if "build-images.sh" in line]
    assert all("--push" not in line for line in build_lines)


def test_the_build_script_knows_the_four_images() -> None:
    run = subprocess.run(  # noqa: S603 - fixed argv list
        [str(BUILD), "--list", "--tag", "1.2.3"], capture_output=True, text=True, check=True
    )
    assert run.stdout.split() == [
        "ghcr.io/kit2code/grader-web:1.2.3",
        "ghcr.io/kit2code/grader-worker:1.2.3",
        "ghcr.io/kit2code/grader-edge:1.2.3",
        "ghcr.io/kit2code/grader-ops:1.2.3",
    ]
