"""Start-up prepared our own cgroup subtree and isolate config (in the real worker container)."""

from conftest import worker

from grader_worker import config

CG_ROOT = "/sys/fs/cgroup/grader-judge"


def read(path: str) -> str:
    result = worker("cat", path)
    assert result.returncode == 0, result.stderr
    return str(result.stdout).strip()


def test_controllers_are_enabled_for_the_boxes() -> None:
    assert set(read(f"{CG_ROOT}/cgroup.subtree_control").split()) >= {"cpuset", "memory", "pids"}


def test_memory_ceiling_is_judge_mem_total_without_swap() -> None:
    assert read(f"{CG_ROOT}/memory.max") == str(1024 * 1024 * 1024)  # JUDGE_MEM_TOTAL=1024M in dev
    assert read(f"{CG_ROOT}/memory.swap.max") == "0"


def test_isolate_config_pins_each_box_to_a_judge_cpu() -> None:
    config = read("/usr/local/etc/isolate")
    lines = [line.strip() for line in config.splitlines()]
    assert "cg_root = /sys/fs/cgroup/grader-judge" in lines
    assert "first_uid = 70000" in lines
    assert "first_gid = 70000" in lines
    # JUDGE_SLOTS=2, JUDGE_BOX_CPUS=10,11 in dev: slot i uses box 100+i on the i-th CPU.
    assert "box100.cpus = 10" in lines
    assert "box101.cpus = 11" in lines
    assert not any(line.startswith("box102.") for line in lines)


def test_a_box_really_runs_on_its_cpu() -> None:
    assert worker("isolate", "--cg", "-b", "101", "--init").returncode == 0
    try:
        limits = ["--cg-mem=65536", "--time=2", "--wall-time=5", "--processes=1", "--fsize=1024"]
        limits += ["--stack=8192", "--meta=/tmp/probe.meta"]
        argv = ["/bin/grep", "Cpus_allowed_list", "/proc/self/status"]
        run = worker("isolate", "--cg", "-b", "101", *limits, "--run", "--", *argv)
        assert run.returncode == 0, run.stderr
        assert run.stdout.split()[-1] == "11"
    finally:
        assert worker("isolate", "--cg", "-b", "101", "--cleanup").returncode == 0


def test_setup_is_idempotent() -> None:
    first = worker("python", "-m", "grader_worker.setup")
    assert first.returncode == 0, first.stderr
    assert read(f"{CG_ROOT}/memory.max") == str(1024 * 1024 * 1024)


def _cpu_set(text: str) -> set[int]:
    """Parse a cgroup CPU list such as "10-11" or "4,6-7"."""
    cpus: set[int] = set()
    for part in text.split(","):
        first, _, last = part.partition("-")
        cpus.update(range(int(first), int(last or first) + 1))
    return cpus


def test_the_whole_subtree_is_limited_to_the_judge_cpus() -> None:
    # JUDGE_BOX_CPUS=10,11 in dev: no box can run elsewhere, whatever its own config says.
    assert _cpu_set(read(f"{CG_ROOT}/cpuset.cpus")) == {10, 11}
    assert _cpu_set(read(f"{CG_ROOT}/cpuset.cpus.effective")) == {10, 11}


def test_the_whole_subtree_has_a_process_ceiling() -> None:
    ceiling = read(f"{CG_ROOT}/pids.max")
    assert ceiling != "max"
    assert 0 < int(ceiling) <= 2 * 64  # JUDGE_SLOTS=2 in dev


def test_setup_leaves_a_small_dev_folder_for_the_boxes() -> None:
    listing = worker("ls", "-A", "/var/local/lib/grader-judge-dev")
    assert listing.returncode == 0, listing.stderr
    assert set(listing.stdout.split()) == {"null", "zero", "full", "random", "urandom", "shm"}


# --- the worker's own cgroup namespace (it never sees or touches the host's cgroups) -------------


def test_the_worker_runs_in_its_own_cgroup_namespace() -> None:
    # Inside a private namespace the worker's processes sit in the leaf "/worker"; with the host's
    # namespace this would be a host path like /docker/<id> or /system.slice/docker-<id>.scope.
    assert read("/proc/self/cgroup") == "0::/worker"


def test_only_our_own_cgroups_are_visible() -> None:
    listing = worker("ls", "/sys/fs/cgroup")
    assert listing.returncode == 0, listing.stderr
    folders = {name for name in listing.stdout.split() if "." not in name}
    assert folders == {"worker", "grader-judge"}, folders  # no docker/, system.slice/, init.scope


def test_no_process_is_left_in_the_namespace_root() -> None:
    # cgroup v2 allows controllers for children only if the parent holds no processes.
    assert read("/sys/fs/cgroup/cgroup.procs") == ""


def test_the_worker_process_and_the_boxes_have_separate_cpus() -> None:
    worker_cpus = config.parse_cpu_list(read("/sys/fs/cgroup/worker/cpuset.cpus.effective"))
    box_cpus = config.parse_cpu_list(read(f"{CG_ROOT}/cpuset.cpus.effective"))
    assert worker_cpus and box_cpus
    assert not set(worker_cpus) & set(box_cpus)
