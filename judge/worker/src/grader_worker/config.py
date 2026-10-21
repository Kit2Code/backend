"""The worker's settings, read from environment variables.

- JUDGE_SLOTS: how many submissions are judged at the same time (1-10, default 2). Slot i uses
  isolate box 100+i, so only boxes 100-109 are ever ours.
- JUDGE_BOX_CPUS: the CPUs reserved for judging, like "4,5" or "4-5". Slot i runs on the i-th one.
- JUDGE_MEM_TOTAL: the memory ceiling for all sandboxes together, like "1024M" (default). At least
  512M, the memory a compile step reserves.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from grader_worker import limits

FIRST_BOX = 100
MAX_SLOTS = 10
# Every step reserves its memory limit from the total first, so the total must at least fit the
# largest fixed step (compiling: 512 MB); a smaller total would make compiles wait forever.
MIN_MEM_TOTAL = max(limits.COMPILE.memory_kb, limits.CHECKER.memory_kb) * 1024

CG_ROOT = Path("/sys/fs/cgroup/grader-judge")
ISOLATE_CONFIG = Path("/usr/local/etc/isolate")
ISOLATE_CONFIG_BASE = Path("/usr/local/etc/isolate.base")
STATE_DIR = Path("/run/grader-judge")
PROBLEMS_DIR = Path("/data/problems")
CACHE_DIR = Path("/data/cache")
TESTLIB_HEADER = Path("/usr/local/share/grader/testlib.h")

_UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}


class ConfigError(ValueError):
    """A setting is missing or invalid; the message names it."""


@dataclass(frozen=True)
class Settings:
    """The validated worker settings."""

    slots: int
    box_cpus: list[int]
    mem_total: int  # bytes

    @property
    def box_ids(self) -> list[int]:
        return [FIRST_BOX + slot for slot in range(self.slots)]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        """Read and check the settings from `env` (normally `os.environ`)."""
        try:
            slots = int(env.get("JUDGE_SLOTS", "2"))
        except ValueError:
            raise ConfigError("JUDGE_SLOTS must be a whole number") from None
        if not 1 <= slots <= MAX_SLOTS:
            raise ConfigError(f"JUDGE_SLOTS must be between 1 and {MAX_SLOTS}")
        if "JUDGE_BOX_CPUS" not in env:
            raise ConfigError("JUDGE_BOX_CPUS must be set, e.g. JUDGE_BOX_CPUS=4,5")
        cpus = parse_cpu_list(env["JUDGE_BOX_CPUS"])
        if len(cpus) < slots:
            raise ConfigError(f"JUDGE_BOX_CPUS lists {len(cpus)} CPUs but JUDGE_SLOTS is {slots}")
        mem_total = parse_memory(env.get("JUDGE_MEM_TOTAL", "1024M"))
        if mem_total < MIN_MEM_TOTAL:
            raise ConfigError(f"JUDGE_MEM_TOTAL must be at least {MIN_MEM_TOTAL >> 20}M")
        return cls(slots=slots, box_cpus=cpus[:slots], mem_total=mem_total)

    def isolate_box_lines(self) -> list[str]:
        """isolate config lines that pin each box to its CPU."""
        return [
            f"box{box}.cpus = {cpu}" for box, cpu in zip(self.box_ids, self.box_cpus, strict=True)
        ]


def parse_memory(text: str) -> int:
    """Parse a size like "1024M", "1G" or "65536K" into bytes."""
    match = re.fullmatch(r"\s*(\d+)\s*([KMGkmg])\s*", text)
    if not match or int(match.group(1)) == 0:
        raise ConfigError(f"{text!r} is not a memory size like 1024M or 1G")
    return int(match.group(1)) * _UNITS[match.group(2).upper()]


def parse_cpu_list(text: str) -> list[int]:
    """Parse a Linux CPU list like "4,5" or "0,2-3" into sorted CPU numbers."""
    cpus: list[int] = []
    for part in text.strip().split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if not match:
            raise ConfigError(f"{text!r} is not a CPU list like 4,5 or 4-5")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        if last < first:
            raise ConfigError(f"{text!r}: range {part.strip()} goes backwards")
        cpus.extend(range(first, last + 1))
    if len(cpus) != len(set(cpus)):
        raise ConfigError(f"{text!r} lists a CPU twice")
    return sorted(cpus)
