"""judge-env prints the environment report of the real worker container as JSON."""

from typing import Any

import pytest
from conftest import worker_json


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    result = worker_json("judge-env", timeout=120)
    assert isinstance(result, dict)
    return result


def test_isolate_and_kernel(report: dict[str, Any]) -> None:
    assert report["isolate"]["version"] == "2.7"
    assert report["kernel"]


def test_cgroup_v2_judge_root(report: dict[str, Any]) -> None:
    assert report["cgroup"]["type"] == "cgroup2"
    assert report["cgroup"]["version"] == 2
    assert report["cgroup"]["writable"] is True
    assert report["cgroup"]["root"] == "/sys/fs/cgroup/grader-judge"
    assert report["cgroup"]["memory_max"] == str(1 << 30)  # JUDGE_MEM_TOTAL=1024M in dev
    assert report["cgroup"]["memory_swap_max"] == "0"
    assert {"cpuset", "memory", "pids"} <= set(report["cgroup"]["subtree_control"])


def test_capabilities_are_shown_raw_and_decoded(report: dict[str, Any]) -> None:
    caps = report["capabilities"]
    assert len(caps["cap_eff"]) == 16
    assert "CAP_SYS_ADMIN" in caps["cap_eff_names"]  # isolate needs it for namespaces and mounts
    assert report["security"]["seccomp"] in {"disabled", "strict", "filter"}


def test_slots_box_map_and_memory(report: dict[str, Any]) -> None:
    # Dev settings: JUDGE_SLOTS=2, JUDGE_BOX_CPUS=10,11, JUDGE_MEM_TOTAL=1024M.
    assert report["slots"] == 2
    assert report["box_map"] == {"100": 10, "101": 11}
    assert report["mem_total"] == 1 << 30
    assert set(report["cpus"]) == {"10", "11"}


def test_isolate_check_environment_ran_read_only(report: dict[str, Any]) -> None:
    check = report["isolate_check_environment"]
    assert isinstance(check["exit_code"], int)
    assert "Checking for" in check["output"]
    assert isinstance(report["warnings"], list)
