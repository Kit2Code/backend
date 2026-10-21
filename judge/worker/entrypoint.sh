#!/bin/sh
# Worker entrypoint: prepare our cgroup subtree and isolate's config (grader_worker.setup), then
# run the given command. Setup failing (a missing cgroup controller, bad JUDGE_* settings) stops
# the container with a clear message instead of judging with the wrong limits.
set -eu

python -m grader_worker.setup
echo "worker: $(isolate --version | head -n 1)"

exec "$@"
