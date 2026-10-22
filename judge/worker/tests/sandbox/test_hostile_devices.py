"""The box sees only a small, fixed /dev (real isolate in the dev stack's worker container).

The worker container can see many of the host's devices (the kernel log, disks, FUSE, KVM, ...).
A box must not: its /dev holds only null, zero, full, random and urandom, plus its own private
/dev/shm. fixtures/hostile/devices.c prints CONTAINED only when that holds and the five devices
still work.
"""

from conftest import FIXTURES_IN_WORKER, worker, worker_json

HOSTILE = f"{FIXTURES_IN_WORKER}/hostile"
PROBLEM = f"{HOSTILE}/problem"
TIMEOUT = 120


def test_the_box_sees_only_the_allowed_devices() -> None:
    # The worker itself has /dev/tty (a non-privileged container has no /dev/kmsg), so the box
    # missing it isn't an accident.
    assert worker("test", "-c", "/dev/tty", timeout=30).returncode == 0
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", "c11", "--source", f"{HOSTILE}/devices.c",
        timeout=TIMEOUT,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    assert len(result["tests"]) == 1, result
    test = result["tests"][0]
    assert test["verdict"] == "AC", f"not contained: {test}"


def test_python_still_runs_with_the_small_dev() -> None:
    # Python reads /dev/urandom at start-up; the hostile problem accepts any program printing
    # CONTAINED.
    source = "import os\nassert len(os.urandom(16)) == 16\nprint('CONTAINED')\n"
    path = "/run/judge-tests/devices_python.py"
    put = worker("sh", "-c", 'mkdir -p /run/judge-tests && cat > "$1"', "sh", path, stdin=source)
    assert put.returncode == 0, put.stderr
    result = worker_json(
        "judge-run", "--problem", PROBLEM, "--lang", "python3", "--source", path, timeout=TIMEOUT
    )
    assert result["verdict"] == "AC", result
