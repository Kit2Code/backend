#!/bin/sh
# Worker entrypoint: prepare our own cgroup subtree for isolate, then run the given command.
# Box cgroups (box-100, box-101, ...) are created by isolate directly under CG_ROOT, so the
# controllers isolate needs must be enabled on the cgroup root and on our subtree.
set -eu

CG_ROOT=/sys/fs/cgroup/grader-judge
CONTROLLERS="cpuset memory pids"

for controller in $CONTROLLERS; do
  if ! grep -qw "$controller" /sys/fs/cgroup/cgroup.controllers; then
    echo "worker: cgroup controller '$controller' is not available on this host" >&2
    exit 1
  fi
  if ! grep -qw "$controller" /sys/fs/cgroup/cgroup.subtree_control; then
    echo "+$controller" > /sys/fs/cgroup/cgroup.subtree_control
  fi
done

mkdir -p "$CG_ROOT"
for controller in $CONTROLLERS; do
  echo "+$controller" > "$CG_ROOT/cgroup.subtree_control"
done

echo "worker: $(isolate --version | head -n 1)"
echo "worker: cgroup root $CG_ROOT ready (controllers: $(cat "$CG_ROOT/cgroup.subtree_control"))"

exec "$@"
