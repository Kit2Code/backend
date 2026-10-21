"""Start-up prepared our own cgroup subtree and isolate config (in the real worker container)."""

from conftest import worker

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
