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
| `write_1gb_many_files.c` | 1 GB as 32 files of 32 MB | stopped by its limits; nothing is left after the box is cleaned |
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
| `memfd_shm.c` | 600 MB in memfds and `/dev/shm` files under a 256 MB limit | MLE (or RE), never AC; no file is left |
| `affinity.c` | more than one CPU, `sched_setaffinity` to every CPU, raising hard resource limits | prints `CONTAINED` (AC) |
| `compile_urandom.cpp` | `#include "/dev/urandom"` | CE within the compile limits; compile log at most 64 KB |
| `compile_error_flood.cpp` | a million compile errors from one line of macros | CE within the compile limits; compile log at most 64 KB |
| `compile_huge_binary.c` | a 1 GB initialised array, so a 1 GB executable | CE (file-size limit); compile log at most 64 KB |
| `devices.c` | opens `/dev/kmsg`, `/dev/mem`, disks, FUSE, vsock, KVM, `/dev/net/tun`, ...; lists `/dev`; makes its own device node (`test_hostile_devices.py`) | prints `CONTAINED` (AC): `/dev` holds only `null`, `zero`, `full`, `random`, `urandom` and `shm`, nothing else opens, and those five still work |
| `whitediff_bomb.c` | 64 MB of `1 ` on one line, for white-diff in the worker (`test_hostile_whitediff.py`) | WA (or OLE/MLE), never JE, within seconds |

They are judged against `problem/` (1 s, 256 MB, expected output `CONTAINED`). `symlink_file_output.c` is judged
against `file-io/` (reads `in.txt`, writes `out.txt`, the answer is the input), and the compile bombs against
`fixtures/problems/aplusb`. `network.c` needs web-admin's IP address put in place of `WEB_ADMIN_IP`, because the
box has no DNS.
