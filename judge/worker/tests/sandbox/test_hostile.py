"""Hostile programs are contained (real isolate in the dev stack's worker container).

Each program in fixtures/hostile/ attempts one kind of escape or overload. The ones that can tell
whether they got out print `CONTAINED` only when every attempt failed, so an AC verdict on the
`hostile` problem (expected output: CONTAINED) means "contained" and anything they did get through
is printed instead and fails the test. The others are stopped by a limit, and the test checks the
right limit stopped them. Some tests also look from outside the box, in the worker, while or after
the program runs: who it runs as, which cgroup it is in, and what it left behind.
"""

import json
import threading
import time
from typing import Any

import pytest
from conftest import FIXTURES_IN_WORKER, REPO, put_source, worker, worker_json

HOSTILE = f"{FIXTURES_IN_WORKER}/hostile"
PROBLEM = f"{HOSTILE}/problem"
ISOLATE_ROOT = "/var/local/lib/isolate"
FIRST_UID = 70000
BOXES = range(100, 110)
# The hostile problem: 1 s time limit, so wall time is 2 x 1 + 1 = 3 s; 256 MB memory; files up to
# 64 MB. Each judge-run (compile + one test) must finish well within this many seconds.
WALL_LIMIT = 3.0
MEMORY_LIMIT_KB = 256 * 1024
FSIZE_KB = 64 * 1024
TIMEOUT = 120


def judge(source: str, lang: str = "c11") -> dict[str, Any]:
    """Judge one hostile program on the hostile problem; return its one test result + box id."""
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", lang, "--source", source, timeout=TIMEOUT
    )
    assert isinstance(result, dict)
    assert result["verdict"] != "CE", result["compile_log"]
    assert result["box_id"] in BOXES
    assert len(result["tests"]) == 1, result
    test = result["tests"][0]
    assert isinstance(test, dict)
    return {**test, "box_id": result["box_id"]}


def assert_contained(test: dict[str, Any]) -> None:
    """The program printed CONTAINED (and nothing else): every attempt failed."""
    assert test["verdict"] == "AC", f"not contained: {test}"


# Lists every process in the worker that runs as a sandbox user or sits in our cgroup subtree.
_SNAPSHOT = """
import json, os
found = []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        status = open(f"/proc/{pid}/status").read().splitlines()
        cgroup = open(f"/proc/{pid}/cgroup").read().strip()
    except OSError:
        continue
    fields = dict(line.split(":", 1) for line in status if ":" in line)
    uid = int(fields["Uid"].split()[0])
    if uid >= 70000 or "grader-judge" in cgroup:
        name = fields["Name"].strip()
        found.append({"pid": int(pid), "uid": uid, "comm": name, "cgroup": cgroup})
print(json.dumps(found))
"""


def judge_while_watching(source: str) -> tuple[dict[str, Any], list[list[dict[str, Any]]]]:
    """Judge `source` and, while it runs, repeatedly list the sandboxed processes in the worker.

    Returns the result and the snapshots. Other jobs may be using the boxes at the same time (and
    the same box just before this job takes it), so a snapshot can show their processes too.
    """
    seen: list[list[dict[str, Any]]] = []
    done = threading.Event()

    def watch() -> None:
        while not done.is_set():
            snapshot = worker("python", "-c", _SNAPSHOT, timeout=30)
            if snapshot.returncode == 0:
                seen.append(json.loads(snapshot.stdout))

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        test = judge(source)
    finally:
        done.set()
        watcher.join(timeout=60)
    return test, seen


def test_fork_bomb_burns_its_cpu_time_alone_and_the_worker_stays_healthy() -> None:
    started = time.monotonic()
    test, seen = judge_while_watching(f"{HOSTILE}/fork_bomb.c")
    assert test["verdict"] == "TLE", test
    assert time.monotonic() - started < 60
    # No snapshot ever showed more than one program process in its box: no fork succeeded.
    box_cgroup = f"0::/grader-judge/box-{test['box_id']}"
    running = [[p for p in snapshot if p["cgroup"] == box_cgroup] for snapshot in seen]
    assert any(running), "the watcher never saw the fork bomb running"
    for processes in running:
        assert len([p for p in processes if p["uid"] >= FIRST_UID]) <= 1, processes
    assert_sandboxed(seen)
    # The worker still runs commands and judges normally afterwards.
    assert worker("true", timeout=30).returncode == 0
    assert_contained(judge(f"{HOSTILE}/spawn.c"))


def test_writing_1gb_to_one_file_stops_at_the_file_size_limit() -> None:
    test = judge(f"{HOSTILE}/write_1gb.c")
    assert test["verdict"] == "OLE", test
    assert_no_file_over_the_size_limit()


def test_writing_1gb_in_many_files_leaves_nothing_behind() -> None:
    test = judge(f"{HOSTILE}/write_1gb_many_files.c")
    assert test["verdict"] != "JE", test
    leftovers = worker("find", ISOLATE_ROOT, "-name", "hostile-fill-*", timeout=60)
    assert leftovers.returncode == 0, leftovers.stderr
    assert leftovers.stdout.strip() == ""
    assert_no_file_over_the_size_limit()


def assert_no_file_over_the_size_limit() -> None:
    big = worker("find", ISOLATE_ROOT, "-type", "f", "-size", f"+{FSIZE_KB}k", timeout=60)
    assert big.returncode == 0, big.stderr
    assert big.stdout.strip() == ""


def test_network_is_unreachable_from_the_box() -> None:
    # Outside the box the worker does reach web-admin:8000, so failing inside isn't an accident.
    lookup = worker("getent", "ahostsv4", "web-admin", timeout=30)
    assert lookup.returncode == 0, lookup.stderr
    web_admin_ip = lookup.stdout.split()[0]
    probe = "import socket; socket.create_connection(('web-admin', 8000), timeout=5).close()"
    assert worker("python", "-c", probe, timeout=30).returncode == 0
    template = (REPO / "fixtures" / "hostile" / "network.c").read_text(encoding="utf-8")
    source = template.replace("WEB_ADMIN_IP", web_admin_ip)
    assert_contained(judge(put_source("hostile-network.c", source)))


def test_secrets_outside_the_box_are_not_readable() -> None:
    assert worker("test", "-r", "/etc/shadow", timeout=30).returncode == 0  # it exists outside
    assert_contained(judge(f"{HOSTILE}/read_secrets.c"))


def test_nothing_can_be_written_outside_the_box() -> None:
    assert_contained(judge(f"{HOSTILE}/write_outside.c"))
    markers = ["/hostile-marker", "/etc/hostile-marker", "/usr/hostile-marker"]
    markers += ["/usr/bin/hostile-marker", "/usr/lib/hostile-marker", "/dev/hostile-marker"]
    # Paths in the worker container that must not exist (nothing is created here).
    markers += ["/dev/shm/hostile-marker", "/tmp/hostile-marker"]  # noqa: S108
    markers += ["/run/hostile-marker"]
    markers += ["/app/hostile-marker", "/data/cache/hostile-marker"]
    markers += [f"{ISOLATE_ROOT}/hostile-marker", "/sys/fs/cgroup/hostile-marker"]
    for marker in markers:
        assert worker("test", "-e", marker, timeout=30).returncode != 0, f"{marker} was created"
    # The box's private /tmp and /dev/shm went away with it.
    left = worker("find", ISOLATE_ROOT, "-name", "hostile-marker", timeout=60)
    assert left.returncode == 0, left.stderr
    assert left.stdout.strip() == ""


def test_sleeping_forever_is_stopped_by_the_wall_time_limit() -> None:
    started = time.monotonic()
    test, seen = judge_while_watching(f"{HOSTILE}/sleep_forever.c")
    assert test["verdict"] == "TLE", test
    assert WALL_LIMIT <= test["wall_time"] <= WALL_LIMIT + 1, test
    assert test["time"] < 0.5, test  # it slept: the wall clock stopped it, not the CPU limit
    assert time.monotonic() - started < 60
    assert any(p["comm"] == "main" for snapshot in seen for p in snapshot), "never saw it run"
    assert_sandboxed(seen)


@pytest.mark.parametrize(("source", "lang"), [("spawn.c", "c11"), ("spawn.py", "python3")])
def test_threads_and_processes_cannot_be_started(source: str, lang: str) -> None:
    assert_contained(judge(f"{HOSTILE}/{source}", lang))


@pytest.mark.parametrize(
    ("source", "lang"), [("memory_600mb.c", "c11"), ("memory_600mb.py", "python3")]
)
def test_600mb_under_a_256mb_limit_is_mle(source: str, lang: str) -> None:
    test = judge(f"{HOSTILE}/{source}", lang)
    assert test["verdict"] == "MLE", test
    assert test["memory_kb"] <= MEMORY_LIMIT_KB, test


def test_the_program_runs_as_a_box_user_in_our_cgroup() -> None:
    # Seen from inside: identity.c checks its own UID, GID and cgroup.
    assert_contained(judge(f"{HOSTILE}/identity.c"))


def assert_sandboxed(snapshots: list[list[dict[str, Any]]]) -> None:
    """Every sandbox-user process is box N's user (70000 + N, N in 100-109) inside
    grader-judge/box-N, and nothing else but isolate's own root helper is in our cgroups."""
    for process in (p for snapshot in snapshots for p in snapshot):
        if process["uid"] >= FIRST_UID:
            box = process["uid"] - FIRST_UID
            assert box in BOXES, process
            assert process["cgroup"] == f"0::/grader-judge/box-{box}", process
        else:
            assert process["uid"] == 0 and process["comm"] == "isolate", process


def test_compilers_and_programs_never_leave_the_namespace() -> None:
    """While programs compile and run, every sandboxed process is in our namespace."""
    _, seen = judge_while_watching(f"{HOSTILE}/sleep_forever.c")
    assert any(p["uid"] >= FIRST_UID for snapshot in seen for p in snapshot)
    assert_sandboxed(seen)
