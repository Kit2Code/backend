"""isolate meta files -> verdicts. Every branch of the mapping has a case here."""

import pytest

from grader_worker.verdicts import Meta, Verdict, outcome_verdict, parse_meta, run_verdict

LIMIT_KB = 256 * 1024
FSIZE_KB = 64 * 1024

# Real isolate 2.7 meta files (shapes from isolate.1.txt, "META-FILES").


def meta(*fields: str) -> str:
    return "\n".join(fields) + "\n"


OK = meta(
    "time:0.012",
    "time-wall:0.034",
    "max-rss:1520",
    "cg-mem:1024",
    "csw-voluntary:2",
    "csw-forced:0",
    "exitcode:0",
)
TIMEOUT = meta(
    "status:TO",
    "message:Time limit exceeded",
    "killed:1",
    "time:1.504",
    "time-wall:1.512",
    "cg-mem:900",
)
WALL_TIMEOUT = meta(
    "status:TO",
    "message:Time limit exceeded (wall clock)",
    "killed:1",
    "time:0.001",
    "time-wall:3.001",
)
XFSZ = meta(
    "status:SG",
    "exitsig:25",
    "message:Caught fatal signal 25",
    "time:0.3",
    "time-wall:0.4",
    "cg-mem:5000",
)
OOM = meta(
    "status:SG",
    "exitsig:9",
    "message:Caught fatal signal 9",
    "cg-oom-killed:1",
    "time:0.2",
    "time-wall:0.3",
    "cg-mem:262144",
)
SEGV = meta(
    "status:SG",
    "exitsig:11",
    "message:Caught fatal signal 11",
    "time:0.01",
    "time-wall:0.02",
    "cg-mem:1200",
)
EXIT_3 = meta(
    "status:RE",
    "exitcode:3",
    "message:Exited with error status 3",
    "time:0.01",
    "time-wall:0.02",
    "cg-mem:1000",
)
INTERNAL = meta("status:XX", "message:Cannot run proxy")


def test_parse_a_normal_run() -> None:
    meta = parse_meta(OK)
    assert meta == Meta(
        status=None,
        message="",
        time=0.012,
        wall_time=0.034,
        max_rss_kb=1520,
        cg_mem_kb=1024,
        cg_oom_killed=False,
        killed=False,
        exitcode=0,
        exitsig=None,
    )


def test_parse_ignores_unknown_keys_and_blank_lines() -> None:
    meta = parse_meta("time:0.5\n\nsome-new-key:abc\nexitcode:0\n")
    assert meta.time == 0.5
    assert meta.exitcode == 0


@pytest.mark.parametrize(
    "text",
    ["time:fast\n", "no colon here\n", "exitcode:\n", "status:ZZ\n", "cg-mem:-5\n"],
)
def test_malformed_meta_is_an_error(text: str) -> None:
    with pytest.raises(ValueError):
        parse_meta(text)


@pytest.mark.parametrize(
    ("text", "output_kb", "expected"),
    [
        (OK, 1, None),  # ran normally: the checker decides
        (TIMEOUT, 0, Verdict.TLE),
        (WALL_TIMEOUT, 0, Verdict.TLE),
        (OOM, 0, Verdict.MLE),
        (SEGV, 0, Verdict.RE),
        (EXIT_3, 0, Verdict.RE),
        (XFSZ, FSIZE_KB, Verdict.OLE),  # C/C++: killed by SIGXFSZ
        (EXIT_3, FSIZE_KB, Verdict.OLE),  # Python ignores SIGXFSZ: write fails, it exits non-zero
        (INTERNAL, 0, Verdict.JE),
    ],
    ids=["ok", "tle", "wall-tle", "oom", "segv", "exit-3", "sigxfsz", "full-output", "xx"],
)
def test_run_verdicts(text: str, output_kb: int, expected: Verdict | None) -> None:
    assert run_verdict(parse_meta(text), LIMIT_KB, output_kb * 1024, FSIZE_KB) == expected


def test_memory_over_the_limit_without_oom_flag_is_mle() -> None:
    # The allocation failed inside the program, which then crashed; peak use reached the limit.
    text = "status:SG\nexitsig:6\ntime:0.1\ntime-wall:0.1\ncg-mem:262145\n"
    assert run_verdict(parse_meta(text), LIMIT_KB, 0, FSIZE_KB) == Verdict.MLE


def test_crash_below_the_memory_limit_is_re() -> None:
    text = "status:SG\nexitsig:6\ntime:0.1\ntime-wall:0.1\ncg-mem:1000\n"
    assert run_verdict(parse_meta(text), LIMIT_KB, 0, FSIZE_KB) == Verdict.RE


def test_time_limit_wins_over_memory() -> None:
    text = "status:TO\nkilled:1\ntime:2.0\ntime-wall:2.0\ncg-mem:262145\n"
    assert run_verdict(parse_meta(text), LIMIT_KB, 0, FSIZE_KB) == Verdict.TLE


def test_normal_exit_with_full_output_is_still_ok() -> None:
    # Exactly the limit is allowed if the program exited normally; the checker decides.
    assert run_verdict(parse_meta(OK), LIMIT_KB, FSIZE_KB * 1024, FSIZE_KB) is None


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (1.0, Verdict.AC),
        (0.0, Verdict.WA),
        (0.5, Verdict.PA),
        (0.999, Verdict.PA),
        (1e-9, Verdict.PA),
    ],
)
def test_outcome_verdicts(outcome: float, expected: Verdict) -> None:
    assert outcome_verdict(outcome) == expected


@pytest.mark.parametrize("outcome", [-0.1, 1.1, float("nan")])
def test_bad_outcomes_are_an_error(outcome: float) -> None:
    with pytest.raises(ValueError):
        outcome_verdict(outcome)
