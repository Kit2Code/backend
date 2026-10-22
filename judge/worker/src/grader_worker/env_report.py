"""The environment report: what the judge host and container look like, as JSON.

    judge-env

prints the report on stdout. It only reads: /proc, /sys, `isolate --version` and
`isolate-check-environment` (never with --execute, which would change host settings). The worker
will also send it to web-admin so the admin System page can show it; on ZimaOS there is no
terminal, so this replaces `docker inspect` and shell checks.

The report holds:
- isolate and kernel versions;
- the cgroup filesystem type, whether /sys/fs/cgroup is mounted writable, and memory.max /
  memory.swap.max of our judge root;
- the effective capabilities (raw `CapEff` and decoded names), the seccomp mode, no_new_privs and
  the AppArmor/LSM label, so the container security profile can be checked on the real host;
- slots, the memory ceiling, the box -> CPU map, and per judge CPU its SMT siblings, P/E kind and
  max MHz;
- warnings: judge CPUs sharing a core, a P/E mix, swap, a missing or unwritable judge cgroup, the
  host's cgroup namespace.
"""

import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from grader_worker import config

# Run a program (argv list) and return (exit code, stdout and stderr together).
Runner = Callable[[list[str]], tuple[int, str]]

MAX_PROGRAM_OUTPUT = 64 * 1024

# Linux capability numbers (include/uapi/linux/capability.h), bit 0 first.
CAPABILITY_NAMES = (
    "CHOWN DAC_OVERRIDE DAC_READ_SEARCH FOWNER FSETID KILL SETGID SETUID SETPCAP LINUX_IMMUTABLE "
    "NET_BIND_SERVICE NET_BROADCAST NET_ADMIN NET_RAW IPC_LOCK IPC_OWNER SYS_MODULE SYS_RAWIO "
    "SYS_CHROOT SYS_PTRACE SYS_PACCT SYS_ADMIN SYS_BOOT SYS_NICE SYS_RESOURCE SYS_TIME "
    "SYS_TTY_CONFIG MKNOD LEASE AUDIT_WRITE AUDIT_CONTROL SETFCAP MAC_OVERRIDE MAC_ADMIN SYSLOG "
    "WAKE_ALARM BLOCK_SUSPEND AUDIT_READ PERFMON BPF CHECKPOINT_RESTORE"
).split()

SECCOMP_MODES = {"0": "disabled", "1": "strict", "2": "filter"}


def status_field(status: str, name: str) -> str | None:
    """The value of one `Name:\\tvalue` line of /proc/<pid>/status, or None."""
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key == name:
            return value.strip()
    return None


def decode_capabilities(hex_mask: str) -> list[str]:
    """Turn a capability mask like "00000000a82425fb" into names like "CAP_CHOWN"."""
    mask = int(hex_mask, 16)
    names = []
    bit = 0
    while mask >> bit:
        if mask >> bit & 1:
            name = CAPABILITY_NAMES[bit] if bit < len(CAPABILITY_NAMES) else str(bit)
            names.append(f"CAP_{name}")
        bit += 1
    return names


def cgroup_mount(mounts: str) -> dict[str, Any]:
    """Type and writability of /sys/fs/cgroup, from /proc/self/mounts (the last mount wins).

    cgroup v2 is one `cgroup2` filesystem; cgroup v1 is a tmpfs holding one mount per controller.
    """
    fstype: str | None = None
    writable = False
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "/sys/fs/cgroup":
            fstype = fields[2]
            writable = "rw" in fields[3].split(",")
    version = {"cgroup2": 2, "tmpfs": 1, "cgroup": 1}.get(fstype or "")
    return {"type": fstype, "version": version, "writable": writable}


def isolate_version(text: str) -> str | None:
    """The version number from `isolate --version` ("The process isolator 2.7" -> "2.7")."""
    match = re.search(r"isolator\s+(\S+)", text)
    return match.group(1) if match else None


def swap_total_kb(meminfo: str) -> int | None:
    """SwapTotal from /proc/meminfo, in kB."""
    value = status_field(meminfo, "SwapTotal")
    if value is None:
        return None
    return int(value.split()[0])


def parse_cpu_list_or_none(text: str | None) -> list[int] | None:
    """Parse a sysfs CPU list like "10-11"; None if missing or not a CPU list."""
    if text is None:
        return None
    try:
        return config.parse_cpu_list(text)
    except config.ConfigError:
        return None


def cgroup_namespace(proc_self_cgroup: str | None) -> str | None:
    """ "private" if the worker has its own cgroup namespace, "host" if it sees the host's.

    In its own namespace the worker's processes are at the namespace root ("/") or in its leaf
    ("/worker"); in the host's they are somewhere like /docker/<id> or /system.slice/....
    """
    if not proc_self_cgroup:
        return None
    path = proc_self_cgroup.strip().split("::", 1)[-1]
    return "private" if path in ("/", "/worker") else "host"


def build_report(
    settings: config.Settings,
    *,
    root: Path = Path("/"),
    run: Runner | None = None,
    kernel: str | None = None,
) -> dict[str, Any]:
    """Collect the report. `root` and `run` are replaced by fakes in unit tests."""
    run = run or _run_program

    def read(path: str) -> str | None:
        try:
            return (root / path.lstrip("/")).read_text()
        except OSError:
            return None

    version_text = _try_run(run, ["isolate", "--version"])[1]
    check_code, check_output = _try_run(run, ["isolate-check-environment"])

    judge_root = str(config.CG_ROOT)
    cgroup = cgroup_mount(read("/proc/self/mounts") or "")
    cgroup["root"] = judge_root
    cgroup["memory_max"] = _strip(read(f"{judge_root}/memory.max"))
    cgroup["memory_swap_max"] = _strip(read(f"{judge_root}/memory.swap.max"))
    cgroup["subtree_control"] = (read(f"{judge_root}/cgroup.subtree_control") or "").split()
    cgroup["namespace"] = cgroup_namespace(read("/proc/self/cgroup"))

    status = read("/proc/self/status") or ""
    cap_eff = status_field(status, "CapEff")
    no_new_privs = status_field(status, "NoNewPrivs")
    lsm_label = read("/proc/self/attr/apparmor/current") or read("/proc/self/attr/current")

    p_cores = parse_cpu_list_or_none(read("/sys/devices/cpu_core/cpus"))
    e_cores = parse_cpu_list_or_none(read("/sys/devices/cpu_atom/cpus"))
    cpus = {
        str(cpu): _cpu_info(cpu, read, p_cores or [], e_cores or []) for cpu in settings.box_cpus
    }
    swap_kb = swap_total_kb(read("/proc/meminfo") or "")

    report: dict[str, Any] = {
        "isolate": {
            "version": isolate_version(version_text),
            "version_line": version_text.splitlines()[0] if version_text else None,
        },
        "kernel": kernel if kernel is not None else platform.release(),
        "cgroup": cgroup,
        "capabilities": {
            "cap_eff": cap_eff,
            "cap_eff_names": decode_capabilities(cap_eff) if cap_eff else [],
        },
        "security": {
            "seccomp": SECCOMP_MODES.get(status_field(status, "Seccomp") or ""),
            "no_new_privs": no_new_privs == "1" if no_new_privs is not None else None,
            "lsm_label": _strip(lsm_label.replace("\0", "") if lsm_label else None),
        },
        "slots": settings.slots,
        "mem_total": settings.mem_total,
        "box_map": {
            str(box): cpu for box, cpu in zip(settings.box_ids, settings.box_cpus, strict=True)
        },
        "cpus": cpus,
        "cpu_topology": {"p_cores": p_cores, "e_cores": e_cores},
        "swap_total_kb": swap_kb,
        "isolate_check_environment": {"exit_code": check_code, "output": check_output},
    }
    report["warnings"] = _warnings(report)
    return report


def _cpu_info(
    cpu: int, read: Callable[[str], str | None], p_cores: list[int], e_cores: list[int]
) -> dict[str, Any]:
    base = f"/sys/devices/system/cpu/cpu{cpu}"
    max_khz = _strip(read(f"{base}/cpufreq/cpuinfo_max_freq"))
    kind = "P" if cpu in p_cores else "E" if cpu in e_cores else None
    return {
        "smt_siblings": parse_cpu_list_or_none(read(f"{base}/topology/thread_siblings_list")),
        "kind": kind,
        "max_mhz": int(max_khz) // 1000 if max_khz and max_khz.isdigit() else None,
    }


def _warnings(report: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    cgroup = report["cgroup"]
    if cgroup["memory_max"] is None:
        warnings.append(f"the judge cgroup {cgroup['root']} is missing or unreadable")
    if not cgroup["writable"]:
        warnings.append("/sys/fs/cgroup is not mounted writable: isolate can't create box cgroups")
    if cgroup.get("namespace") == "host":
        warnings.append(
            "the worker uses the host's cgroup namespace: root in the worker could reach other "
            "containers' cgroups (use `cgroup: private`)"
        )
    if cgroup["memory_swap_max"] not in (None, "0"):
        warnings.append(
            f"memory.swap.max of the judge cgroup is {cgroup['memory_swap_max']}, not 0: "
            "boxes may swap"
        )

    judge_cpus = [int(cpu) for cpu in report["cpus"]]
    for index, cpu in enumerate(judge_cpus):
        siblings = report["cpus"][str(cpu)]["smt_siblings"] or [cpu]
        for other in judge_cpus[index + 1 :]:
            if other in siblings:
                warnings.append(
                    f"judge CPUs {cpu} and {other} share a physical core (SMT siblings): "
                    "their run times disturb each other"
                )
    kinds = {info["kind"] for info in report["cpus"].values()}
    if {"P", "E"} <= kinds:
        warnings.append("the judge CPUs mix P-cores and E-cores: run times differ between slots")

    swap_kb = report["swap_total_kb"]
    if swap_kb:
        warnings.append(
            f"swap is on ({swap_kb >> 10} MiB): boxes can't use it (memory.swap.max = 0), but "
            "host swapping can still add timing noise"
        )
    return warnings


def _strip(text: str | None) -> str | None:
    return text.strip() if text is not None else None


def _try_run(run: Runner, argv: list[str]) -> tuple[int | None, str]:
    try:
        return run(argv)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"could not run {argv[0]}: {error}"


def _run_program(argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603 - fixed argv list, no shell, no outside input
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=60,
        check=False,
    )
    return result.returncode, result.stdout[:MAX_PROGRAM_OUTPUT]


def judge_env(argv: Sequence[str] | None = None) -> int:
    """`judge-env`: print the environment report as JSON (exit 0), or a settings error (exit 2)."""
    del argv  # no options yet
    try:
        settings = config.Settings.from_env(os.environ)
    except config.ConfigError as error:
        print(f"judge-env: error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(build_report(settings), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(judge_env())
