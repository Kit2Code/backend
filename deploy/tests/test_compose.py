"""deploy/zimaos/grader.compose.yml follows the rules for a ZimaOS "import compose" install.

Published images only (no build), literal values (no ${VAR}), data only under
/DATA/AppData/grader, secret folders (never single files), healthchecks, restart policies, log
limits, CPU pinning (no CPU quotas), memory limits, internal networks, one published port, and
no real hostname or address of the owner's server.
"""

import re
import shutil
import subprocess
from typing import Any

import pytest
import yaml

from deploy.tests.conftest import REPO

COMPOSE = REPO / "deploy" / "zimaos" / "grader.compose.yml"
DOCKER = shutil.which("docker") or "docker"
DATA = "/DATA/AppData/grader/"
OURS = re.compile(r"ghcr\.io/kit2code/grader-(web|worker|edge|ops):\d+\.\d+\.\d+")
PINNED = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")
APP_CPUS, JUDGE_BOX_CPUS = "3", "4,5"


def compose() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def services() -> dict[str, dict[str, Any]]:
    return dict(compose()["services"])


LONG_RUNNING = [name for name, service in services().items() if service.get("restart") != "no"]


def test_docker_compose_accepts_it() -> None:
    run = subprocess.run(  # noqa: S603 - fixed argv list
        [DOCKER, "compose", "-f", str(COMPOSE), "config", "-q"],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr


def test_literal_values_only() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "${" not in text and "build:" not in text


def test_the_owners_real_addresses_are_not_in_git() -> None:
    text = COMPOSE.read_text(encoding="utf-8").lower()
    # Only placeholders: no LAN addresses or tailnet names, and the hostname/admin address below.
    for private in ("192.168.", "10.0.", "ts.net"):
        assert private not in text, private
    env = services()["web"]["environment"]
    assert env["PUBLIC_BASE_URL"] == "https://grader.example.org"  # replaced in the ZimaOS UI
    assert services()["web-admin"]["environment"]["ADMIN_BASE_URL"] == "http://192.0.2.10:8081"
    assert services()["tunnel"]["environment"]["TUNNEL_TOKEN"] == "PASTE_IN_ZIMAOS_UI_NOT_IN_GIT"


@pytest.mark.parametrize("name", list(services()))
def test_images_are_published_and_pinned(name: str) -> None:
    image = services()[name]["image"]
    assert OURS.fullmatch(image) or PINNED.fullmatch(image), image


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_every_service_is_watched_restarted_and_limited(name: str) -> None:
    service = services()[name]
    assert service["restart"] == "unless-stopped"
    assert "test" in service["healthcheck"], name
    assert service["logging"] == {
        "driver": "json-file", "options": {"max-size": "10m", "max-file": "3"},
    }  # fmt: skip
    assert service["mem_limit"]
    assert "cpus" not in service and "cpu_quota" not in service and "deploy" not in service


@pytest.mark.parametrize("name", list(services()))
def test_cpu_pinning(name: str) -> None:
    service = services()[name]
    if service.get("network_mode") == "none":
        return  # secrets-init: a one-shot that runs for a second
    assert service["cpuset"] == APP_CPUS, name


def test_judge_boxes_run_on_their_own_cpus() -> None:
    assert services()["worker"]["environment"]["JUDGE_BOX_CPUS"] == JUDGE_BOX_CPUS


@pytest.mark.parametrize("name", list(services()))
def test_data_only_under_appdata_and_secret_folders(name: str) -> None:
    for volume in services()[name].get("volumes", []):
        source = volume.split(":")[0]
        # Only data folders: no host system paths (bind-mounting /sys/fs/cgroup even broke isolate).
        assert source.startswith(DATA), volume
        if "/secrets" in source and source != DATA + "secrets":
            # A folder per service, never a single secret file.
            assert re.fullmatch(re.escape(DATA) + r"secrets/(db|web|worker|backup)", source), volume
            assert volume.endswith(":ro"), volume


def test_networks() -> None:
    networks = compose()["networks"]
    for name in ("app_net", "db_net", "judge_net"):
        assert networks[name]["internal"] is True
    assert networks["edge_net"].get("internal") is not True  # the tunnel needs the internet
    assert networks["app_net"]["ipam"]["config"] == [{"subnet": "10.89.91.0/24"}]
    fixed = {name: s["networks"]["app_net"]["ipv4_address"]
             for name, s in services().items()
             if isinstance(s.get("networks"), dict) and "app_net" in s["networks"]}  # fmt: skip
    assert fixed == {"edge": "10.89.91.10", "web": "10.89.91.20", "web-admin": "10.89.91.21"}


def test_only_the_admin_port_is_published() -> None:
    published = {name: s["ports"] for name, s in services().items() if "ports" in s}
    assert published == {"edge": ["8081:8081"]}


def test_the_worker_has_the_narrow_profile_and_one_secret() -> None:
    worker = services()["worker"]
    assert "privileged" not in worker
    # NET_ADMIN: isolate switches on the loopback interface in each sandbox's network namespace
    # (with SYS_ADMIN alone: "SIOCSIFFLAGS on 'lo' failed: Operation not permitted").
    assert worker["cap_add"] == ["SYS_ADMIN", "NET_ADMIN"]
    assert worker["cgroup"] == "host"
    assert sorted(worker["security_opt"]) == ["apparmor=unconfined", "seccomp=unconfined"]
    assert worker["networks"] == ["judge_net"]
    assert worker["hostname"] == "grader-worker"  # a stable worker id across updates
    mounts = [v.split(":")[1] for v in worker["volumes"]]
    assert "/run/secrets/worker" in mounts and "/run/secrets/web" not in mounts
    assert not any("docker.sock" in v for s in services().values() for v in s.get("volumes", []))


def test_the_casaos_metadata() -> None:
    meta = compose()["x-casaos"]
    assert meta["main"] == "edge" and meta["port_map"] == "8081" and meta["index"] == "/admin/"
    assert meta["architectures"] == ["amd64"]
    assert "en_US" in meta["title"]
