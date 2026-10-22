"""Disk limits per box (grader_worker.diskwatch): too much kept on, or written to, disk is OLE."""

from conftest import FIXTURES_IN_WORKER, worker, worker_json

PROBLEM = f"{FIXTURES_IN_WORKER}/hostile/problem"


def judge(source: str) -> dict[str, object]:
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/{source}", timeout=180,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    return dict(result["tests"][0])


def test_the_boxes_have_io_accounting() -> None:
    result = worker("cat", "/sys/fs/cgroup/grader-judge/cgroup.subtree_control")
    assert "io" in result.stdout.split()


def test_keeping_1gb_on_disk_is_stopped_at_the_space_limit() -> None:
    test = judge("write_1gb_many_files.c")
    assert test["verdict"] == "OLE", test
    assert "256 MB" in str(test["message"])
    leftovers = worker("find", "/var/local/lib/isolate", "-name", "hostile-fill-*")
    assert leftovers.stdout.strip() == ""


def test_writing_and_deleting_over_and_over_is_stopped() -> None:
    test = judge("disk_churn.c")
    assert test["verdict"] == "OLE", test
    assert "wrote more than 512 MB" in str(test["message"])
