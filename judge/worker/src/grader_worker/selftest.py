"""The self-test: judge the hostile programs on this host and check each one was contained.

The same programs and expectations as the sandbox tests (fixtures/hostile/README.md), run by the
real judge on the real server, so the admin System page can show that the sandbox holds there too.
Only cases whose outcome shows in the verdict are here; the sandbox tests also look from outside
the box (processes, cgroups, leftover files), which needs the dev stack.

Every case runs with the problem's own limits (time scale 1.0): the expectations depend on them.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grader_common.api import JudgeResult, SelfTestCase, SelfTestReport, VerdictCode
from grader_common.importer.errors import ProblemImportError
from grader_worker.isolate import IsolateError
from grader_worker.judge import SubmissionError

HOSTILE_PROBLEM = "hostile/problem"  # 1 s, 256 MB, expected output CONTAINED
FILE_IO_PROBLEM = "hostile/file-io"  # reads in.txt, writes out.txt; the answer is the input
APLUSB = "problems/aplusb"
CONTAINED: tuple[VerdictCode, ...] = ("AC",)  # the program printed CONTAINED: every attempt failed
# Stopped by a limit, or allowed to finish inside the box (AC): anything but a judge error. What
# matters is that nothing escapes and nothing is left, as the sandbox test checks.
STOPPED: tuple[VerdictCode, ...] = ("WA", "TLE", "OLE", "MLE", "RE", "AC")


@dataclass(frozen=True)
class Case:
    name: str
    source: str  # relative to the fixtures folder
    lang: str
    problem: str  # relative to the fixtures folder
    allowed: tuple[VerdictCode, ...]


CASES = (
    Case("aplusb_before", "submissions/aplusb/ac.c", "c11", APLUSB, ("AC",)),
    Case("fork_bomb", "hostile/fork_bomb.c", "c11", HOSTILE_PROBLEM, ("TLE",)),
    Case("write_1gb", "hostile/write_1gb.c", "c11", HOSTILE_PROBLEM, ("OLE",)),
    Case("write_1gb_many_files", "hostile/write_1gb_many_files.c", "c11", HOSTILE_PROBLEM, STOPPED),
    Case("network", "hostile/network.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("read_secrets", "hostile/read_secrets.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("write_outside", "hostile/write_outside.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("sleep_forever", "hostile/sleep_forever.c", "c11", HOSTILE_PROBLEM, ("TLE",)),
    Case("spawn_c", "hostile/spawn.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("spawn_py", "hostile/spawn.py", "python3", HOSTILE_PROBLEM, CONTAINED),
    Case("memory_600mb_c", "hostile/memory_600mb.c", "c11", HOSTILE_PROBLEM, ("MLE",)),
    Case("memory_600mb_py", "hostile/memory_600mb.py", "python3", HOSTILE_PROBLEM, ("MLE",)),
    Case("identity", "hostile/identity.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("setuid_exec", "hostile/setuid_exec.c", "c11", HOSTILE_PROBLEM, ("RE", "AC")),
    Case("kill_all", "hostile/kill_all.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("fifo_output", "hostile/fifo_output.c", "c11", HOSTILE_PROBLEM, ("WA",)),
    Case("symlink_output", "hostile/symlink_output.c", "c11", HOSTILE_PROBLEM, ("WA",)),
    Case("symlink_file_output", "hostile/symlink_file_output.c", "c11", FILE_IO_PROBLEM, ("WA",)),
    Case("memfd_shm", "hostile/memfd_shm.c", "c11", HOSTILE_PROBLEM, ("MLE", "RE", "TLE")),
    Case("affinity", "hostile/affinity.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case("devices", "hostile/devices.c", "c11", HOSTILE_PROBLEM, CONTAINED),
    Case(
        "whitediff_bomb", "hostile/whitediff_bomb.c", "c11", HOSTILE_PROBLEM, ("WA", "OLE", "MLE")
    ),
    Case("compile_urandom", "hostile/compile_urandom.cpp", "cpp17", APLUSB, ("CE",)),
    Case("compile_error_flood", "hostile/compile_error_flood.cpp", "cpp17", APLUSB, ("CE",)),
    Case("compile_huge_binary", "hostile/compile_huge_binary.c", "c11", APLUSB, ("CE",)),
    Case("aplusb_after", "submissions/aplusb/ac.c", "c11", APLUSB, ("AC",)),
)

MAX_MESSAGE = 4096


def run(
    fixtures: Path, slot: Any, judge_fn: Callable[..., JudgeResult], web_admin_ip: str
) -> SelfTestReport:
    """Judge every case in `slot` and compare its verdict with the ones that mean "contained".

    `web_admin_ip` goes into network.c, which tries to reach web-admin (the box has no DNS).
    """
    if not (fixtures / HOSTILE_PROBLEM / "problem.yaml").is_file():
        raise SubmissionError(f"the self-test fixtures are not in {fixtures}")
    return SelfTestReport(
        kind="selftest",
        cases=[_run_case(case, fixtures, slot, judge_fn, web_admin_ip) for case in CASES],
    )


def _run_case(
    case: Case, fixtures: Path, slot: Any, judge_fn: Callable[..., JudgeResult], web_admin_ip: str
) -> SelfTestCase:
    started = time.monotonic()
    expected = list(case.allowed)
    try:
        source = (fixtures / case.source).read_bytes()
        source = source.replace(b"WEB_ADMIN_IP", web_admin_ip.encode("ascii"))
        result = judge_fn(fixtures / case.problem, case.lang, source, slot, 1.0)
    except (SubmissionError, ProblemImportError, IsolateError, OSError) as error:
        return SelfTestCase(
            name=case.name, expected=expected, verdict="JE", ok=False,
            seconds=_since(started), message=str(error)[:MAX_MESSAGE],
        )  # fmt: skip
    ok = result.verdict in case.allowed
    message = "" if ok else _explain(result)
    return SelfTestCase(
        name=case.name, expected=expected, verdict=result.verdict, ok=ok,
        seconds=_since(started), message=message[:MAX_MESSAGE],
    )  # fmt: skip


def _since(started: float) -> float:
    return round(min(time.monotonic() - started, 3600.0), 2)


def _explain(result: JudgeResult) -> str:
    """What the program printed or why it stopped: the first test's message, or the compile log."""
    if result.verdict == "CE":
        return result.compile_log
    if result.tests and result.tests[0].message:
        return result.tests[0].message
    return result.message
