"""Follow-ups of the M6H sandbox audit: the box filter is really box-exec's, it covers the calls
Docker's filter lets through, and the disk watch can't be hidden from."""

import pytest
from conftest import FIXTURES_IN_WORKER, worker_json

PROBLEM = f"{FIXTURES_IN_WORKER}/hostile/problem"


def judge(source: str) -> dict[str, object]:
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/{source}", timeout=240,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    return dict(result["tests"][0])


@pytest.mark.parametrize("source", ["launcher_present.c", "syslog_read.c", "clone_ns.c"])
def test_the_box_filter_denies_it(source: str) -> None:
    test = judge(source)
    assert test["verdict"] == "AC", f"not contained: {test}"


def test_other_system_call_numbers_kill_the_program() -> None:
    test = judge("x32_syscall.c")
    assert test["verdict"] == "RE", test


@pytest.mark.parametrize("source", ["disk_deep_tree.c", "disk_unlinked.c", "inode_flood.c"])
def test_hidden_disk_use_is_stopped(source: str) -> None:
    # Stopped by a disk limit (OLE) or by the time limit (TLE), whichever comes first; never AC.
    # Which one depends on the disk's speed; the disk watch's own checks are unit tests.
    test = judge(source)
    assert test["verdict"] in ("OLE", "TLE"), test
