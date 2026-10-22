"""Is the worker healthy? Used by `judge-health`, the container's healthcheck.

The judge loop touches an "alive" file in the state folder every time round (at least every
30 s, even while web-admin is away). The worker is unhealthy if that file is missing or old (the
loop has stopped or hangs), or if every slot is marked broken (isolate couldn't clean a box; only a
restart clears that).
"""

import time
from pathlib import Path

from grader_worker import config
from grader_worker.slots import BROKEN_PREFIX

ALIVE_NAME = "alive"
# Longer than the loop's longest wait (30 s backoff while web-admin is away), with room to spare.
MAX_AGE_SECONDS = 90


def touch_alive(state_dir: Path) -> None:
    """Called by the judge loop: "still going"."""
    state_dir.mkdir(mode=0o700, exist_ok=True)
    (state_dir / ALIVE_NAME).touch()


def problems(settings: config.Settings, state_dir: Path) -> list[str]:
    """What is wrong with the worker; an empty list if it is healthy."""
    found = []
    try:
        age = time.time() - (state_dir / ALIVE_NAME).stat().st_mtime
    except FileNotFoundError:
        found.append("the judge loop has not started")
    else:
        if age > MAX_AGE_SECONDS:
            found.append(f"the judge loop has been stuck for {age:.0f} s")
    if all((state_dir / f"{BROKEN_PREFIX}{index}").exists() for index in range(settings.slots)):
        found.append("every judge slot is broken; restart the worker")
    return found
