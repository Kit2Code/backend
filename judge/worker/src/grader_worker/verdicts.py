"""Verdicts, decided from isolate's meta file (never from its human-readable message).

The meta file format is described in isolate v2.7's manual (isolate.1.txt, "META-FILES"): one
`key:value` per line. `status` is RE (non-zero exit), SG (killed by a signal), TO (timed out) or
XX (isolate's own error), and is missing after a normal exit. `cg-oom-killed` is present when the
memory cgroup's OOM killer stopped the program; `cg-mem` is the peak memory of the box in KB.
"""

import math
import signal
from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    AC = "AC"  # accepted (outcome 1)
    PA = "PA"  # partially accepted (0 < outcome < 1)
    WA = "WA"  # wrong answer (outcome 0)
    TLE = "TLE"  # time limit exceeded
    MLE = "MLE"  # memory limit exceeded
    RE = "RE"  # runtime error: non-zero exit or killed by a signal
    OLE = "OLE"  # output limit exceeded
    JE = "JE"  # judge error: our problem, not the contestant's
    CE = "CE"  # compilation error (for the whole submission)


@dataclass(frozen=True)
class Meta:
    """The fields of one isolate meta file that the judge uses."""

    status: str | None
    message: str
    time: float
    wall_time: float
    max_rss_kb: int | None
    cg_mem_kb: int | None
    cg_oom_killed: bool
    killed: bool
    exitcode: int | None
    exitsig: int | None


_STATUSES = {"RE", "SG", "TO", "XX"}


def parse_meta(text: str) -> Meta:
    """Parse a meta file. Unknown keys are ignored; malformed values raise ValueError."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, colon, value = line.partition(":")
        if not colon:
            raise ValueError(f"meta file line without ':': {line!r}")
        fields[key.strip()] = value.strip()
    status = fields.get("status")
    if status is not None and status not in _STATUSES:
        raise ValueError(f"unknown isolate status {status!r}")
    return Meta(
        status=status,
        message=fields.get("message", ""),
        time=_number(fields, "time"),
        wall_time=_number(fields, "time-wall"),
        max_rss_kb=_whole(fields, "max-rss"),
        cg_mem_kb=_whole(fields, "cg-mem"),
        cg_oom_killed="cg-oom-killed" in fields,
        killed="killed" in fields,
        exitcode=_whole(fields, "exitcode"),
        exitsig=_whole(fields, "exitsig"),
    )


def run_verdict(
    meta: Meta, memory_limit_kb: int, output_bytes: int, fsize_kb: int
) -> Verdict | None:
    """The verdict of a contestant run, or None if it ended normally and the checker decides.

    Checked in this order: isolate's own failure (JE), time (TLE), memory (MLE), output size (OLE),
    any other failure (RE). Output counts as over the limit when the program died from SIGXFSZ, or
    failed while its output file had reached the size limit (Python ignores SIGXFSZ, so its write
    fails instead and it exits with an error).
    """
    if meta.status == "XX":
        return Verdict.JE
    if meta.status == "TO":
        return Verdict.TLE
    if meta.status is None:
        return None
    if meta.cg_oom_killed or (meta.cg_mem_kb is not None and meta.cg_mem_kb > memory_limit_kb):
        return Verdict.MLE
    if meta.exitsig == signal.SIGXFSZ or output_bytes >= fsize_kb * 1024:
        return Verdict.OLE
    return Verdict.RE


def outcome_verdict(outcome: float) -> Verdict:
    """AC, PA or WA from a checker's outcome between 0 and 1."""
    if not (math.isfinite(outcome) and 0.0 <= outcome <= 1.0):
        raise ValueError(f"outcome {outcome!r} is not between 0 and 1")
    if outcome == 1.0:
        return Verdict.AC
    if outcome == 0.0:
        return Verdict.WA
    return Verdict.PA


def _number(fields: dict[str, str], key: str) -> float:
    value = fields.get(key, "0")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"meta field {key} is not a number: {value!r}") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"meta field {key} is out of range: {value!r}")
    return number


def _whole(fields: dict[str, str], key: str) -> int | None:
    if key not in fields:
        return None
    value = fields[key]
    if not value.isdigit():
        raise ValueError(f"meta field {key} is not a whole number: {value!r}")
    return int(value)
