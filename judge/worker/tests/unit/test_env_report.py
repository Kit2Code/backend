"""Building the environment report from (fake) /proc and /sys contents."""

from pathlib import Path

import pytest

from grader_worker.config import Settings
from grader_worker.env_report import (
    build_report,
    cgroup_mount,
    decode_capabilities,
    isolate_version,
    parse_cpu_list_or_none,
    status_field,
    swap_total_kb,
)

# CapEff here is Docker's default set plus CAP_SYS_ADMIN.
STATUS = """\
Name:\tpython
NoNewPrivs:\t0
CapInh:\t0000000000000000
CapPrm:\t000001ffffffffff
CapEff:\t00000000a82425fb
CapBnd:\t000001ffffffffff
Seccomp:\t2
"""

MOUNTS_V2 = """\
overlay / overlay rw,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
sysfs /sys sysfs ro,nosuid,nodev,noexec,relatime 0 0
cgroup /sys/fs/cgroup cgroup2 ro,nosuid,nodev,noexec,relatime 0 0
cgroup /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime 0 0
"""

MOUNTS_V1 = """\
tmpfs /sys/fs/cgroup tmpfs ro,nosuid,nodev,noexec,mode=755 0 0
cgroup /sys/fs/cgroup/memory cgroup rw,nosuid,nodev,noexec,relatime,memory 0 0
"""

SETTINGS = Settings(slots=2, box_cpus=[10, 11], mem_total=1 << 30)


def test_status_field() -> None:
    assert status_field(STATUS, "CapEff") == "00000000a82425fb"
    assert status_field(STATUS, "Seccomp") == "2"
    assert status_field(STATUS, "Missing") is None


def test_docker_default_capabilities_decode_to_names() -> None:
    # 0xa80425fb is Docker's default capability set.
    assert decode_capabilities("00000000a80425fb") == [
        "CAP_CHOWN",
        "CAP_DAC_OVERRIDE",
        "CAP_FOWNER",
        "CAP_FSETID",
        "CAP_KILL",
        "CAP_SETGID",
        "CAP_SETUID",
        "CAP_SETPCAP",
        "CAP_NET_BIND_SERVICE",
        "CAP_NET_RAW",
        "CAP_SYS_CHROOT",
        "CAP_MKNOD",
        "CAP_AUDIT_WRITE",
        "CAP_SETFCAP",
    ]


def test_all_capabilities_and_unknown_bits() -> None:
    names = decode_capabilities("000001ffffffffff")
    assert len(names) == 41
    assert "CAP_SYS_ADMIN" in names
    assert names[-1] == "CAP_CHECKPOINT_RESTORE"
    assert decode_capabilities(f"{1 << 45:016x}") == ["CAP_45"]
    assert decode_capabilities("0000000000000000") == []


def test_cgroup_v2_mount_uses_the_last_entry() -> None:
    assert cgroup_mount(MOUNTS_V2) == {"type": "cgroup2", "version": 2, "writable": True}


def test_cgroup_v1_is_a_tmpfs_of_controllers() -> None:
    assert cgroup_mount(MOUNTS_V1) == {"type": "tmpfs", "version": 1, "writable": False}


def test_no_cgroup_mount() -> None:
    assert cgroup_mount("proc /proc proc rw 0 0\n") == {
        "type": None,
        "version": None,
        "writable": False,
    }


def test_isolate_version_line() -> None:
    text = "The process isolator 2.7\n(c) 2012--2026 Martin Mares\nBuilt on ...\n"
    assert isolate_version(text) == "2.7"
    assert isolate_version("garbage") is None


def test_swap_total() -> None:
    assert swap_total_kb("MemTotal: 100 kB\nSwapTotal:       2097152 kB\n") == 2097152
    assert swap_total_kb("MemTotal: 100 kB\n") is None


def test_cpu_list_or_none() -> None:
    assert parse_cpu_list_or_none("10-11\n") == [10, 11]
    assert parse_cpu_list_or_none(None) is None
    assert parse_cpu_list_or_none("nonsense") is None


def write(root: Path, path: str, text: str) -> None:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def fake_host(root: Path, *, siblings: dict[int, str], swap_kb: int) -> None:
    write(root, "/proc/self/status", STATUS)
    write(root, "/proc/self/mounts", MOUNTS_V2)
    write(root, "/proc/meminfo", f"MemTotal: 16000000 kB\nSwapTotal: {swap_kb} kB\n")
    write(root, "/sys/fs/cgroup/grader-judge/memory.max", "1073741824\n")
    write(root, "/sys/fs/cgroup/grader-judge/memory.swap.max", "0\n")
    write(root, "/sys/fs/cgroup/grader-judge/cgroup.subtree_control", "cpuset memory pids\n")
    for cpu, text in siblings.items():
        write(root, f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list", text)
        write(root, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq", "4700000\n")


def fake_run(argv: list[str]) -> tuple[int, str]:
    if argv[:2] == ["isolate", "--version"]:
        return 0, "The process isolator 2.7\n"
    assert argv == ["isolate-check-environment"], "never --execute"
    return 1, "Checking for swap ... CAUTION\n"


def test_report_on_a_clean_host(tmp_path: Path) -> None:
    fake_host(tmp_path, siblings={10: "10\n", 11: "11\n"}, swap_kb=0)
    report = build_report(SETTINGS, root=tmp_path, run=fake_run, kernel="6.8.0")

    assert report["isolate"] == {"version": "2.7", "version_line": "The process isolator 2.7"}
    assert report["kernel"] == "6.8.0"
    assert report["cgroup"]["type"] == "cgroup2"
    assert report["cgroup"]["writable"] is True
    assert report["cgroup"]["memory_max"] == "1073741824"
    assert report["cgroup"]["memory_swap_max"] == "0"
    assert report["cgroup"]["subtree_control"] == ["cpuset", "memory", "pids"]
    assert report["capabilities"]["cap_eff"] == "00000000a82425fb"
    assert "CAP_SYS_ADMIN" in report["capabilities"]["cap_eff_names"]
    assert report["security"]["seccomp"] == "filter"
    assert report["security"]["no_new_privs"] is False
    assert report["slots"] == 2
    assert report["mem_total"] == 1 << 30
    assert report["box_map"] == {"100": 10, "101": 11}
    assert report["cpus"]["10"] == {"smt_siblings": [10], "kind": None, "max_mhz": 4700}
    assert report["isolate_check_environment"] == {
        "exit_code": 1,
        "output": "Checking for swap ... CAUTION\n",
    }
    assert report["warnings"] == []


def test_report_warns_about_shared_cores_swap_and_pe_mix(tmp_path: Path) -> None:
    fake_host(tmp_path, siblings={10: "10-11\n", 11: "10-11\n"}, swap_kb=2097152)
    write(tmp_path, "/sys/devices/cpu_core/cpus", "0-10\n")
    write(tmp_path, "/sys/devices/cpu_atom/cpus", "11-15\n")
    report = build_report(SETTINGS, root=tmp_path, run=fake_run, kernel="6.8.0")

    assert report["cpu_topology"] == {"p_cores": list(range(11)), "e_cores": [11, 12, 13, 14, 15]}
    assert report["cpus"]["10"]["kind"] == "P"
    assert report["cpus"]["11"]["kind"] == "E"
    warnings = " ".join(report["warnings"])
    assert "10 and 11 share a physical core" in warnings
    assert "swap" in warnings
    assert "P-cores and E-cores" in warnings


def test_report_warns_when_box_swap_is_allowed(tmp_path: Path) -> None:
    fake_host(tmp_path, siblings={10: "10\n", 11: "11\n"}, swap_kb=0)
    write(tmp_path, "/sys/fs/cgroup/grader-judge/memory.swap.max", "max\n")
    report = build_report(SETTINGS, root=tmp_path, run=fake_run, kernel="6.8.0")
    assert any("memory.swap.max" in warning for warning in report["warnings"])


def test_report_survives_a_missing_cgroup_and_isolate(tmp_path: Path) -> None:
    write(tmp_path, "/proc/self/status", STATUS)

    def no_programs(argv: list[str]) -> tuple[int, str]:
        raise FileNotFoundError(argv[0])

    report = build_report(SETTINGS, root=tmp_path, run=no_programs, kernel="6.8.0")
    assert report["isolate"]["version"] is None
    assert report["cgroup"]["memory_max"] is None
    assert report["isolate_check_environment"]["exit_code"] is None
    assert any("grader-judge" in warning for warning in report["warnings"])


@pytest.mark.parametrize(("value", "mode"), [("0", "disabled"), ("1", "strict"), ("2", "filter")])
def test_seccomp_modes(tmp_path: Path, value: str, mode: str) -> None:
    write(tmp_path, "/proc/self/status", STATUS.replace("Seccomp:\t2", f"Seccomp:\t{value}"))
    report = build_report(SETTINGS, root=tmp_path, run=fake_run, kernel="6.8.0")
    assert report["security"]["seccomp"] == mode


@pytest.mark.parametrize(
    ("cgroup", "namespace"),
    [
        ("0::/worker\n", "private"),
        ("0::/\n", "private"),
        ("0::/docker/0123abcd\n", "host"),
        ("0::/system.slice/docker-0123abcd.scope\n", "host"),
    ],
)
def test_the_report_says_which_cgroup_namespace_the_worker_is_in(
    tmp_path: Path, cgroup: str, namespace: str
) -> None:
    fake_host(tmp_path, siblings={10: "10\n", 11: "11\n"}, swap_kb=0)
    write(tmp_path, "/proc/self/cgroup", cgroup)
    report = build_report(SETTINGS, root=tmp_path, run=fake_run, kernel="6.8.0")
    assert report["cgroup"]["namespace"] == namespace
    warned = any("cgroup namespace" in warning for warning in report["warnings"])
    assert warned == (namespace == "host")
