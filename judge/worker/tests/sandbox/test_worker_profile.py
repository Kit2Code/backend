"""The worker container has only the capabilities isolate needs, and can't gain more."""

import pytest
from conftest import FIXTURES_IN_WORKER, worker, worker_json

from grader_worker.env_report import decode_capabilities

# What isolate needs (docs/DECISIONS.md, "Worker profile"): namespaces and mounts, the box's
# loopback, switching to the box users, chroot, owning and cleaning box files. (Box processes are
# stopped through their cgroup, so no CAP_KILL; tried one by one, each of these is needed.)
EXPECTED = {
    "CAP_SYS_ADMIN", "CAP_NET_ADMIN", "CAP_SETUID", "CAP_SETGID", "CAP_CHOWN", "CAP_DAC_OVERRIDE",
    "CAP_FOWNER", "CAP_SYS_CHROOT",
}  # fmt: skip


def status(field: str) -> str:
    result = worker("grep", f"^{field}:", "/proc/1/status")
    assert result.returncode == 0, result.stderr
    return str(result.stdout).split(":", 1)[1].strip()


def test_the_worker_has_exactly_the_capabilities_isolate_needs() -> None:
    names = set(decode_capabilities(status("CapEff")))
    assert names == EXPECTED, (names - EXPECTED, EXPECTED - names)
    assert "CAP_MKNOD" not in names and "CAP_NET_RAW" not in names


def test_the_worker_can_never_gain_privileges() -> None:
    assert status("NoNewPrivs") == "1"


@pytest.mark.parametrize("program", ["mknod_dev.c"])
def test_a_box_cannot_make_device_nodes(program: str) -> None:
    result = worker_json(
        "judge-run", "--problem", f"{FIXTURES_IN_WORKER}/hostile/problem", "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/{program}", timeout=120,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    assert result["tests"][0]["verdict"] == "AC", f"not contained: {result['tests'][0]}"
