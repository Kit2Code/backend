# Hostile programs

Programs that try to escape or overload the sandbox: fork bombs, huge writes, network access, reading or writing
files outside the sandbox, sleeping forever, spawning processes, and allocating too much memory. The sandbox tests
(`judge/worker/tests/sandbox/test_hostile.py`) judge each one and check that it was contained. They are also baked
into the worker image so the self-test can run them on the real server.

They are tests, not examples. Never weaken a limit to make one of them pass.

| Program | What it tries | Contained when |
|---|---|---|
| `fork_bomb.c` | forks forever | TLE; never more than one process in its box; the worker still judges afterwards |
| `write_1gb.c` | 1 GB to stdout | OLE at the 64 MB file-size limit; no file over the limit is left |
| `write_1gb_many_files.c` | 1 GB as 32 files of 32 MB | OLE at the box's disk-space limit (`test_hostile_disk.py`); nothing is left after the box is cleaned |
| `disk_churn.c` | writes a 60 MB file, forces it to disk, deletes it, again and again | OLE at the box's limit on bytes written to disk (`test_hostile_disk.py`) |
| `network.c` | TCP/UDP to `1.1.1.1:53`, TCP to `web-admin:8000`, any non-loopback interface | prints `CONTAINED` (AC) |
| `read_secrets.c` | `/etc/shadow`, the worker's `/proc/1/*`, secrets, fixtures, problems | prints `CONTAINED` (AC) |
| `write_outside.c` | creating files in `/`, system folders, isolate's and the worker's folders | prints `CONTAINED` (AC); no marker exists in the worker afterwards |
| `sleep_forever.c` | sleeps without using CPU | TLE by wall time (3 s for a 1 s limit) |
| `spawn.c`, `spawn.py` | a thread, a fork, running `/bin/true` | prints `CONTAINED` (AC) |
| `memory_600mb.c`, `memory_600mb.py` | 600 MB under a 256 MB limit | MLE |
| `identity.c` | checks its own UID/GID (70000+box) and cgroup (`grader-judge/box-1NN`) | prints `CONTAINED` (AC) |
| `setuid_exec.c` | setuid files on a suid mount, `setuid(0)`, then runs `isolate --box-id=100 --cleanup`, `su` or `mount` | RE (the program refused, unprivileged) or AC (nothing to run); never WA |
| `kill_all.c` | `kill(-1, SIGKILL)` and `kill(1, SIGKILL)` | prints `CONTAINED` (AC); the worker still judges `aplusb` afterwards |
| `fifo_output.c` | replaces its `_stdout` file with a FIFO | WA (no output), without the judge hanging |
| `symlink_output.c` | replaces its `_stdout` file with a symlink (to a decoy holding the answer, `/etc/shadow`, `/proc/1/environ`) | WA (no output): the link is never followed |
| `symlink_file_output.c` | on `file-io/` (answer = input), makes `out.txt` a symlink to `in.txt` | WA (no output), never AC |
| `memfd_shm.c` | 600 MB in memfds and `/dev/shm` files under a 256 MB limit | MLE (or RE, or TLE on a busy or slow CPU), never AC; no file is left |
| `affinity.c` | more than one CPU, `sched_setaffinity` to every CPU, raising hard resource limits | prints `CONTAINED` (AC) |
| `compile_urandom.cpp` | `#include "/dev/urandom"` | CE within the compile limits; compile log at most 64 KB |
| `compile_error_flood.cpp` | a million compile errors from one line of macros | CE within the compile limits; compile log at most 64 KB |
| `compile_huge_binary.c` | a 1 GB initialised array, so a 1 GB executable | CE (file-size limit); compile log at most 64 KB |
| `devices.c` | opens `/dev/kmsg`, `/dev/mem`, disks, FUSE, vsock, KVM, `/dev/net/tun`, ...; lists `/dev`; makes its own device node (`test_hostile_devices.py`) | prints `CONTAINED` (AC): `/dev` holds only `null`, `zero`, `full`, `random`, `urandom` and `shm`, nothing else opens, and those five still work |
| `cgroup_escape.c` | writes to `cgroup.procs`, `memory.max`, `cgroup.kill`, `cgroup.freeze` (directly, climbing up, through `/proc`) and mounts its own cgroup2 (`test_hostile_cgroup.py`) | prints `CONTAINED` (AC) |
| `mknod_dev.c` | makes device nodes for a disk, the kernel log, memory and a loop device, then opens them (`test_worker_profile.py`) | prints `CONTAINED` (AC) |
| `seccomp_surface.c` | io_uring, bpf, userfaultfd, keyctl/add_key, perf_event_open, a new user namespace, ptrace on its parent (`test_hostile_seccomp.py`) | prints `CONTAINED` (AC): the box launcher's seccomp filter makes each call fail |
| `proc_sys_write.c` | opens `/proc/sys/kernel/core_pattern`, `/proc/sysrq-trigger` and other kernel settings for writing; unmounts, mounts over or remounts `/proc/sys` (`test_hostile_seccomp.py`) | prints `CONTAINED` (AC) |
| `launcher_present.c` | `ptrace(PTRACE_TRACEME)`, which only the box's own filter (box-exec) denies (`test_hostile_audit.py`) | prints `CONTAINED` (AC): proves box-exec is in front of the program |
| `syslog_read.c` | reads the host's kernel log (`syslog` READ_ALL) | prints `CONTAINED` (AC) |
| `clone_ns.c` | clone/clone3 with each CLONE_NEW* flag, fsopen, open_tree, move_mount, memfd_secret, pidfd_getfd, kcmp | prints `CONTAINED` (AC) |
| `x32_syscall.c` | an x32 system call and a 32-bit `int 0x80` call | RE: the filter kills it (SIGSYS) |
| `disk_deep_tree.c` | 2200 nested folders (longer than PATH_MAX), then 64 MB files at the bottom | OLE or TLE: a folder the watch can't read stops the box |
| `disk_unlinked.c` | writes 64 MB to files it already deleted (still open) | OLE (bytes written) or TLE |
| `inode_flood.c` | empty files until stopped | OLE (too many files) or TLE |
| `whitediff_bomb.c` | 64 MB of `1 ` on one line, for white-diff in the worker (`test_hostile_whitediff.py`) | WA (or OLE/MLE), never JE, within seconds |

They are judged against `problem/` (1 s, 256 MB, expected output `CONTAINED`). `symlink_file_output.c` is judged
against `file-io/` (reads `in.txt`, writes `out.txt`, the answer is the input), and the compile bombs against
`fixtures/problems/aplusb`. `network.c` needs web-admin's IP address put in place of `WEB_ADMIN_IP`, because the
box has no DNS.
