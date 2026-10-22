"""Prepare the host for judging; the container entrypoint runs this before anything else.

Following the isolate manual's "Control groups" setup without systemd (isolate v2.7, isolate.1.txt
and isolate-cg-keeper.c), but in our own subtree so the CMS isolate on the same host is never
touched:

1. enable the cpuset, memory and pids controllers down to /sys/fs/cgroup/grader-judge, where
   isolate creates one cgroup per box;
2. cap the memory of all boxes together at JUDGE_MEM_TOTAL, with no swap; keep the whole subtree
   on the judge CPUs (JUDGE_BOX_CPUS) and under a process ceiling, so a box can't get more even if
   its own settings were wrong;
3. write isolate's config: our base settings plus one `box1NN.cpus` line per slot;
4. make the boxes' small /dev (isolate.BOX_DEV_DIR): null, zero, full, random, urandom (the
   worker's own devices, bind-mounted: no mknod) and an empty shm folder for isolate's /dev/shm;
5. clear the judge state left by an earlier run of the container: the memory ledger and the
   broken-slot marks (no judging can run before this).

The worker runs in its own cgroup namespace (`cgroup: private`): /sys/fs/cgroup is the container's
own cgroup, and nothing of the host's or other containers' is visible. Before step 1, every process
at that root moves into a leaf, `worker` (cgroup v2 lets a cgroup hand controllers to its children
only while it holds no processes), and that leaf is pinned to the worker's own CPU (WORKER_CPUS, by
default the container's first CPU that isn't a judge CPU).

It only ever writes inside the container's own cgroup, to isolate's config file, to BOX_DEV_DIR and
to the state folder. Safe to run again.
"""

import fcntl
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from grader_worker import config, limits
from grader_worker.isolate import BOX_DEV_DIR, BOX_DEVICES
from grader_worker.slots import BROKEN_PREFIX, LEDGER_NAME

CONTROLLERS = ("cpuset", "memory", "pids")
OPTIONAL_CONTROLLERS = ("io",)
MOUNT = "/usr/bin/mount"
UMOUNT = "/usr/bin/umount"
NAMESPACE_ROOT = config.CG_ROOT.parent  # /sys/fs/cgroup: the container's own cgroup
WORKER_LEAF = "worker"
# Process ceiling of the whole subtree, per slot: twice the most any step allows (compilers),
# which leaves room for isolate's own helper in each box cgroup.
PIDS_PER_SLOT = 2 * limits.COMPILE.processes


def check_private_namespace(self_cgroup: Path = Path("/proc/self/cgroup")) -> None:
    """Refuse to start with the host's cgroup namespace (then root here could reach the host's
    and other containers' cgroups)."""
    path = self_cgroup.read_text().strip().split("::", 1)[-1]
    if path not in ("/", f"/{WORKER_LEAF}"):
        raise config.ConfigError(
            f"the worker needs its own cgroup namespace (cgroup: private), but runs in {path}"
        )


def enter_worker_leaf(root: Path = NAMESPACE_ROOT) -> None:
    """Move every process at the namespace root into the leaf `worker`."""
    leaf = root / WORKER_LEAF
    leaf.mkdir(exist_ok=True)
    for _ in range(100):  # processes may start meanwhile (a `docker exec`); try a few rounds
        pids = (root / "cgroup.procs").read_text().split()
        if not pids:
            return
        for pid in pids:
            try:
                (leaf / "cgroup.procs").write_text(f"{pid}\n")
            except ProcessLookupError:
                pass  # it ended meanwhile
    raise config.ConfigError(f"could not move every process out of {root}")


def worker_cpus(
    settings: config.Settings, env: Mapping[str, str], root: Path = NAMESPACE_ROOT
) -> list[int]:
    """The CPUs of the worker process itself: WORKER_CPUS, or the container's first CPU that
    isn't a judge CPU. Never one of the judge's (they belong to the boxes)."""
    if env.get("WORKER_CPUS"):
        cpus = config.parse_cpu_list(env["WORKER_CPUS"])
    else:
        allowed = config.parse_cpu_list((root / "cpuset.cpus.effective").read_text().strip())
        free = [cpu for cpu in allowed if cpu not in settings.box_cpus]
        if not free:
            raise config.ConfigError(
                "the container has no CPU besides the judge CPUs: give it one more (cpuset)"
            )
        cpus = free[:1]
    if set(cpus) & set(settings.box_cpus):
        raise config.ConfigError("WORKER_CPUS must not include a judge CPU (JUDGE_BOX_CPUS)")
    return cpus


def pin_worker_leaf(cpus: list[int], root: Path = NAMESPACE_ROOT) -> None:
    (root / WORKER_LEAF / "cpuset.cpus").write_text(",".join(str(cpu) for cpu in cpus) + "\n")


def prepare_cgroup(settings: config.Settings, root: Path = config.CG_ROOT) -> None:
    """Create our cgroup subtree with the controllers isolate needs and the memory ceiling."""
    host_root = root.parent
    available = (host_root / "cgroup.controllers").read_text().split()
    for controller in CONTROLLERS:
        if controller not in available:
            raise config.ConfigError(
                f"cgroup controller {controller!r} is not available on this host"
            )
    # io, when the host has it: the disk watch reads each box's bytes written (io.stat).
    wanted = CONTROLLERS + tuple(c for c in OPTIONAL_CONTROLLERS if c in available)
    _enable_controllers(host_root, wanted)
    root.mkdir(exist_ok=True)
    _enable_controllers(root, wanted)
    (root / "memory.max").write_text(f"{settings.mem_total}\n")
    (root / "memory.swap.max").write_text("0\n")
    (root / "cpuset.cpus").write_text(",".join(str(cpu) for cpu in settings.box_cpus) + "\n")
    (root / "pids.max").write_text(f"{settings.slots * PIDS_PER_SLOT}\n")


def write_isolate_config(
    settings: config.Settings,
    base: Path = config.ISOLATE_CONFIG_BASE,
    target: Path = config.ISOLATE_CONFIG,
) -> None:
    """isolate's config = our baked base settings + one CPU line per box."""
    lines = base.read_text().rstrip("\n").splitlines()
    lines += ["", "# Generated at start-up from JUDGE_SLOTS and JUDGE_BOX_CPUS."]
    lines += settings.isolate_box_lines()
    temporary = target.with_suffix(".new")
    temporary.write_text("\n".join(lines) + "\n")
    os.replace(temporary, target)


def make_box_dev(target: Path = Path(BOX_DEV_DIR), source: Path = Path("/dev")) -> None:
    """Create the boxes' /dev: a root-owned folder with the worker's own five harmless character
    devices bind-mounted onto empty files (no mknod: the worker has no CAP_MKNOD) and an empty
    `shm` folder. Anything else in it is removed. Devices already in place are kept, so boxes
    running meanwhile are not disturbed."""
    if target.is_symlink():
        raise OSError(f"{target} is a symbolic link")
    target.mkdir(mode=0o755, exist_ok=True)
    os.chown(target, 0, 0)
    # Read-only for boxes; this also drops a set-group-ID bit inherited from the parent.
    os.chmod(target, 0o755)  # noqa: S103
    for entry in target.iterdir():
        if entry.name not in (*BOX_DEVICES, "shm"):
            _remove(entry)
    for name in BOX_DEVICES:
        _bind_device(target / name, source / name)
    shm = target / "shm"
    if shm.is_symlink() or (shm.exists() and not shm.is_dir()):
        _remove(shm)
    shm.mkdir(mode=0o755, exist_ok=True)
    os.chown(shm, 0, 0)
    os.chmod(shm, 0o755)  # noqa: S103 - read-only for boxes; isolate mounts a tmpfs on it


def _bind_device(path: Path, device: Path) -> None:
    """Make `path` show the worker's character device `device` (a bind mount on an empty file)."""
    original = os.stat(device)
    if not stat.S_ISCHR(original.st_mode):
        raise OSError(f"{device} is not a character device in the worker")
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        current = None
    if current is not None and stat.S_ISCHR(current.st_mode):
        if current.st_rdev == original.st_rdev:
            return  # already the right device
        _unmount(path)
        current = os.lstat(path) if path.exists() else None
    if current is None or not stat.S_ISREG(current.st_mode):
        if current is not None:
            _remove(path)
        path.touch(mode=0o644)
    subprocess.run(  # noqa: S603 - fixed argv list
        [MOUNT, "--bind", str(device), str(path)], check=True, capture_output=True
    )


def _unmount(path: Path) -> None:
    subprocess.run([UMOUNT, str(path)], check=False, capture_output=True)  # noqa: S603


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        for child in path.iterdir():
            _remove(child)
        path.rmdir()
    else:
        path.unlink()


def reset_state(state_dir: Path = config.STATE_DIR) -> None:
    """Forget the memory ledger and broken-slot marks of an earlier run of the container."""
    state_dir.mkdir(mode=0o700, exist_ok=True)
    ledger = state_dir / LEDGER_NAME
    lock_fd = os.open(ledger.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ledger.unlink(missing_ok=True)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    for mark in state_dir.glob(f"{BROKEN_PREFIX}*"):
        mark.unlink(missing_ok=True)


def _enable_controllers(cgroup: Path, controllers: tuple[str, ...] = CONTROLLERS) -> None:
    control = cgroup / "cgroup.subtree_control"
    enabled = control.read_text().split()
    for controller in controllers:
        if controller not in enabled:
            control.write_text(f"+{controller}\n")


def main() -> int:
    try:
        settings = config.Settings.from_env(os.environ)
        check_private_namespace()
        enter_worker_leaf()
        prepare_cgroup(settings)
        pin_worker_leaf(worker_cpus(settings, os.environ))
        write_isolate_config(settings)
        make_box_dev()
        reset_state()
    except (config.ConfigError, OSError) as error:
        print(f"worker setup: {error}", file=sys.stderr)
        return 1
    print(
        f"worker setup: {settings.slots} slot(s), boxes {settings.box_ids}, "
        f"CPUs {settings.box_cpus}, memory ceiling {settings.mem_total >> 20} MiB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
