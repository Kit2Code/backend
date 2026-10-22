"""Release safety: nothing is pushed before the checks pass, the pushed digests are printed for
pinning the ZimaOS compose, and no workflow keeps the token in the checkout."""

import os
import re
import subprocess
from pathlib import Path

from deploy.tests.test_release import BUILD, CI, RELEASE, load

DIGEST = "sha256:" + "ab" * 32
PINNED = re.compile(r"ghcr\.io/kit2code/grader-(web|worker|edge|ops):0\.1\.1@sha256:[0-9a-f]{64}")


def test_the_release_runs_the_same_checks_as_ci_before_pushing() -> None:
    release, ci = load(RELEASE)["jobs"], load(CI)["jobs"]
    check = release["check"]
    assert check["env"] == ci["check"]["env"]
    assert check["services"] == ci["check"]["services"]
    runs = [step.get("run") for step in check["steps"]]
    assert runs == [step.get("run") for step in ci["check"]["steps"]]
    assert "./scripts/check.sh" in runs
    assert release["images"]["needs"] == ["check"]


def test_the_release_prints_the_pushed_digests() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    assert "--digests" in text and "GITHUB_STEP_SUMMARY" in text


def test_no_checkout_keeps_the_token() -> None:
    for workflow in (load(RELEASE), load(CI)):
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step.get("with", {}).get("persist-credentials") is False, step


def test_the_build_script_writes_the_digests(tmp_path: Path) -> None:
    # A stand-in for docker: builds do nothing, pushes answer like the registry does.
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(
        f'#!/bin/sh\n[ "$1" = push ] && echo "${{2##*:}}: digest: {DIGEST} size: 1234"\nexit 0\n'
    )
    fake.chmod(0o755)
    digests = tmp_path / "digests.txt"
    env = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}"}
    run = subprocess.run(  # noqa: S603 - fixed argv list
        [str(BUILD), "--push", "--tag", "0.1.1", "--tag", "sha-abc1234", "--digests", str(digests)],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr
    lines = digests.read_text().split()
    assert len(lines) == 4 and all(PINNED.fullmatch(line) for line in lines), lines
    assert all(line.endswith(DIGEST) for line in lines)


def test_a_push_without_a_digest_fails(tmp_path: Path) -> None:
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}"}
    run = subprocess.run(  # noqa: S603 - fixed argv list
        [str(BUILD), "--push", "--tag", "0.1.1", "--digests", str(tmp_path / "d.txt")],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )  # fmt: skip
    assert run.returncode != 0


def test_the_check_job_may_only_read() -> None:
    assert load(RELEASE)["jobs"]["check"]["permissions"] == {"contents": "read"}
