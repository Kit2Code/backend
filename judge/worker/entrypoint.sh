#!/bin/sh
# Worker entrypoint: prepare our cgroup subtree and isolate's config (grader_worker.setup), then
# run the given command. Setup failing (a missing cgroup controller, bad JUDGE_* settings) stops
# the container with a clear message instead of judging with the wrong limits.
set -eu

# Without `privileged`, Docker mounts the cgroup tree read-only; isolate must create the boxes'
# cgroups, so mount it read-write again (needs CAP_SYS_ADMIN). Bind-mounting the host's
# /sys/fs/cgroup instead breaks isolate (docs/DECISIONS.md, "Worker profile outcome").
if grep -q ' /sys/fs/cgroup cgroup2 ro,' /proc/mounts; then
  mount -o remount,rw /sys/fs/cgroup
fi

python -m grader_worker.setup
echo "worker: $(isolate --version | head -n 1)"

exec "$@"
