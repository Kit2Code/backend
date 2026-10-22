"""The worker's settings, read from environment variables.

- JUDGE_SLOTS: how many submissions are judged at the same time (1-10, default 2). Slot i uses
  isolate box 100+i, so only boxes 100-109 are ever ours.
- JUDGE_BOX_CPUS: the CPUs reserved for judging, like "4,5" or "4-5". Slot i runs on the i-th one.
- JUDGE_MEM_TOTAL: the memory ceiling for all sandboxes together, like "1024M" (default). At least
  512M, the memory a compile step reserves.
- JUDGE_BOX_DISK_MB / JUDGE_BOX_WRITE_MB: per box, the most disk space its files may use (256) and
  the most it may write to disk in one run (512); see grader_worker.diskwatch.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from grader_worker import isolate, limits

FIRST_BOX = 100
MAX_SLOTS = 10
# Every step reserves its memory limit from the total first, so the total must at least fit the
# largest fixed step (compiling: 512 MB); a smaller total would make compiles wait forever.
MIN_MEM_TOTAL = max(limits.COMPILE.memory_kb, limits.CHECKER.memory_kb) * 1024

CG_ROOT = isolate.CG_ROOT
ISOLATE_CONFIG = Path("/usr/local/etc/isolate")
ISOLATE_CONFIG_BASE = Path("/usr/local/etc/isolate.base")
STATE_DIR = Path("/run/grader-judge")
PROBLEMS_DIR = Path("/data/problems")
CACHE_DIR = Path("/data/cache")
TESTLIB_HEADER = Path("/usr/local/share/grader/testlib.h")
# The self-test's programs and problems, baked into the image (a copy of parts of fixtures/).
FIXTURES_DIR = Path("/opt/grader/fixtures")

_UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}


class ConfigError(ValueError):
    """A setting is missing or invalid; the message names it."""


@dataclass(frozen=True)
class Settings:
    """The validated worker settings."""

    slots: int
    box_cpus: list[int]
    mem_total: int  # bytes
    # Disk limits per box (grader_worker.diskwatch): space kept, and bytes written to disk.
    box_disk_bytes: int = 256 << 20
    box_write_bytes: int = 512 << 20

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
        disk = _megabytes(env, "JUDGE_BOX_DISK_MB", 256)
        written = _megabytes(env, "JUDGE_BOX_WRITE_MB", 512)
        # A program may write one file up to the output limit, so the box needs more than that.
        least = 2 * limits.OUTPUT_FSIZE_KB // 1024
        for name, value in (("JUDGE_BOX_DISK_MB", disk), ("JUDGE_BOX_WRITE_MB", written)):
            if value < least << 20:
                raise ConfigError(f"{name} must be at least {least}")
        return cls(
            slots=slots, box_cpus=cpus[:slots], mem_total=mem_total,
            box_disk_bytes=disk, box_write_bytes=written,
        )  # fmt: skip

    def isolate_box_lines(self) -> list[str]:
        """isolate config lines that pin each box to its CPU."""
        return [
            f"box{box}.cpus = {cpu}" for box, cpu in zip(self.box_ids, self.box_cpus, strict=True)
        ]


def _megabytes(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError:
        raise ConfigError(f"{name} must be a whole number of megabytes") from None
    return value << 20


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
