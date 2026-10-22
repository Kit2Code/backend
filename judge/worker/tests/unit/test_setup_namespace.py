"""The worker's start-up in its own cgroup namespace: refuse the host's, pick the worker's CPU."""

from pathlib import Path

import pytest

from grader_worker import config, setup

SETTINGS = config.Settings(slots=2, box_cpus=[4, 5], mem_total=1 << 30)


def fake_namespace(tmp_path: Path, self_cgroup: str, effective_cpus: str = "3-5") -> Path:
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cpuset.cpus.effective").write_text(effective_cpus + "\n")
    (tmp_path / "self_cgroup").write_text(self_cgroup)
    return root


def test_the_hosts_cgroup_namespace_is_refused(tmp_path: Path) -> None:
    fake_namespace(tmp_path, "0::/system.slice/docker-0123.scope\n")
    with pytest.raises(config.ConfigError, match="cgroup: private"):
        setup.check_private_namespace(tmp_path / "self_cgroup")


@pytest.mark.parametrize("self_cgroup", ["0::/\n", "0::/worker\n"])
def test_a_private_namespace_is_accepted(tmp_path: Path, self_cgroup: str) -> None:
    fake_namespace(tmp_path, self_cgroup)
    setup.check_private_namespace(tmp_path / "self_cgroup")


def test_the_worker_gets_the_containers_first_cpu_that_is_not_a_judge_cpu(tmp_path: Path) -> None:
    root = fake_namespace(tmp_path, "0::/\n", effective_cpus="3-5")
    assert setup.worker_cpus(SETTINGS, {}, root) == [3]


def test_worker_cpus_can_be_set(tmp_path: Path) -> None:
    root = fake_namespace(tmp_path, "0::/\n", effective_cpus="2-5")
    assert setup.worker_cpus(SETTINGS, {"WORKER_CPUS": "2-3"}, root) == [2, 3]


@pytest.mark.parametrize(("env", "effective"), [({"WORKER_CPUS": "4"}, "3-5"), ({}, "4-5")])
def test_the_worker_never_shares_a_judge_cpu(
    tmp_path: Path, env: dict[str, str], effective: str
) -> None:
    root = fake_namespace(tmp_path, "0::/\n", effective_cpus=effective)
    with pytest.raises(config.ConfigError, match="judge"):
        setup.worker_cpus(SETTINGS, env, root)
