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
4. make the boxes' small /dev (isolate.BOX_DEV_DIR): null, zero, full, random, urandom and an
   empty shm folder for isolate's /dev/shm;
5. clear the judge state left by an earlier run of the container: the memory ledger and the
   broken-slot marks (no judging can run before this).

It only ever writes inside /sys/fs/cgroup/grader-judge, to the root's cgroup.subtree_control
(adding controllers), to isolate's config file, to BOX_DEV_DIR and to the state folder. Safe to
run again.
"""

import fcntl
import os
import stat
import sys
from pathlib import Path

from grader_worker import config, limits
from grader_worker.isolate import BOX_DEV_DIR, BOX_DEVICES
from grader_worker.slots import BROKEN_PREFIX, LEDGER_NAME

CONTROLLERS = ("cpuset", "memory", "pids")
# Process ceiling of the whole subtree, per slot: twice the most any step allows (compilers),
# which leaves room for isolate's own helper in each box cgroup.
PIDS_PER_SLOT = 2 * limits.COMPILE.processes


def prepare_cgroup(settings: config.Settings, root: Path = config.CG_ROOT) -> None:
    """Create our cgroup subtree with the controllers isolate needs and the memory ceiling."""
    host_root = root.parent
    available = (host_root / "cgroup.controllers").read_text().split()
    for controller in CONTROLLERS:
        if controller not in available:
            raise config.ConfigError(
                f"cgroup controller {controller!r} is not available on this host"
            )
    _enable_controllers(host_root)
    root.mkdir(exist_ok=True)
    _enable_controllers(root)
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
    """Create the boxes' /dev: a root-owned folder with copies of five harmless character
    devices (same major/minor as the worker's) and an empty `shm` folder. Anything else in it is
    removed. Existing correct entries are kept, so boxes running meanwhile are not disturbed."""
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
        _make_device(target / name, os.stat(source / name))
    shm = target / "shm"
    if shm.is_symlink() or (shm.exists() and not shm.is_dir()):
        _remove(shm)
    shm.mkdir(mode=0o755, exist_ok=True)
    os.chown(shm, 0, 0)
    os.chmod(shm, 0o755)  # noqa: S103 - read-only for boxes; isolate mounts a tmpfs on it


def _make_device(path: Path, original: os.stat_result) -> None:
    if not stat.S_ISCHR(original.st_mode):
        raise OSError(f"{path.name} is not a character device in the worker")
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        current = None
    if (
        current is not None
        and stat.S_ISCHR(current.st_mode)
        and current.st_rdev == original.st_rdev
        and stat.S_IMODE(current.st_mode) == 0o666
        and current.st_uid == 0
        and current.st_gid == 0
    ):
        return
    temporary = path.with_name(f".{path.name}.new")
    temporary.unlink(missing_ok=True)
    os.mknod(temporary, stat.S_IFCHR | 0o666, original.st_rdev)
    os.chown(temporary, 0, 0)
    # mknod's mode is reduced by the umask. null, zero, ... are world-usable by design.
    os.chmod(temporary, 0o666)  # noqa: S103
    os.replace(temporary, path)


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


def _enable_controllers(cgroup: Path) -> None:
    control = cgroup / "cgroup.subtree_control"
    enabled = control.read_text().split()
    for controller in CONTROLLERS:
        if controller not in enabled:
            control.write_text(f"+{controller}\n")


def main() -> int:
    try:
        settings = config.Settings.from_env(os.environ)
        prepare_cgroup(settings)
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
