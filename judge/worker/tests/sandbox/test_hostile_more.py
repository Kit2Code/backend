"""More hostile programs: setuid programs, killing everything, tampering with the output file,
memory outside the heap, CPU pinning and resource limits, and compile bombs.

Same rules as test_hostile.py: the programs that can tell whether they got out print
`CONTAINED` only when every attempt failed (AC on the hostile problem means contained); the
others must be stopped with the right verdict, quickly, and leave the worker healthy.
"""

import time
from typing import Any

import pytest
from conftest import FIXTURES_IN_WORKER, REPO, put_source, worker, worker_json

HOSTILE = f"{FIXTURES_IN_WORKER}/hostile"
PROBLEM = f"{HOSTILE}/problem"
FILE_IO_PROBLEM = f"{HOSTILE}/file-io"
APLUSB = f"{FIXTURES_IN_WORKER}/problems/aplusb"
ISOLATE_ROOT = "/var/local/lib/isolate"
BOXES = range(100, 110)
MEMORY_LIMIT_KB = 256 * 1024
MAX_LOG_BYTES = 64 * 1024
TIMEOUT = 120


def judge_all(problem: str, source: str, lang: str = "c11") -> dict[str, Any]:
    """Judge `source` on `problem` and return the whole result."""
    result = worker_json(
        "judge-run", "--problem", problem, "--lang", lang, "--source", source, timeout=TIMEOUT
    )
    assert isinstance(result, dict)
    assert result["box_id"] in BOXES
    return result


def judge(source: str, lang: str = "c11", problem: str = PROBLEM) -> dict[str, Any]:
    """Judge one hostile program (that compiles) and return its one test result."""
    result = judge_all(problem, source, lang)
    assert result["verdict"] != "CE", result["compile_log"]
    assert len(result["tests"]) == 1, result
    test = result["tests"][0]
    assert isinstance(test, dict)
    return test


def fixture_with(name: str, old: str, new: str) -> str:
    """Put a copy of fixtures/hostile/<name> with `old` replaced by `new` into the worker."""
    text = (REPO / "fixtures" / "hostile" / name).read_text(encoding="utf-8")
    assert old in text
    return put_source(f"hostile-{len(new)}-{abs(hash(new))}-{name}", text.replace(old, new))


def assert_worker_still_judges() -> None:
    """The worker runs commands and judges an ordinary correct solution normally."""
    assert worker("true", timeout=30).returncode == 0
    ok = judge_all(APLUSB, f"{FIXTURES_IN_WORKER}/submissions/aplusb/ac.c")
    assert ok["verdict"] == "AC", ok


# --- setuid programs ------------------------------------------------------------------------

DEFAULT_TARGET = '{"isolate", "--box-id=100", "--cleanup", NULL}'


@pytest.mark.parametrize(
    "target",
    [
        None,  # `isolate --box-id=100 --cleanup`, found on PATH
        '{"/usr/local/bin/isolate", "--box-id=100", "--cleanup", NULL}',
        '{"/usr/bin/su", "root", "-c", "true", NULL}',
        '{"/usr/bin/mount", "-t", "tmpfs", "none", "/tmp", NULL}',
    ],
)
def test_setuid_programs_do_not_give_root(target: str | None) -> None:
    source = f"{HOSTILE}/setuid_exec.c"
    if target is not None:
        source = fixture_with("setuid_exec.c", DEFAULT_TARGET, target)
    test = judge(source)
    # RE: the target ran unprivileged and refused. AC: no target could be started.
    # WA (or anything else) means it found a usable setuid file, or the target ran as root.
    assert test["verdict"] in ("RE", "AC"), f"not contained: {test}"


# --- killing everything ---------------------------------------------------------------------


def test_killing_every_process_only_reaches_the_box() -> None:
    test = judge(f"{HOSTILE}/kill_all.c")
    assert test["verdict"] == "AC", f"not contained: {test}"
    assert_worker_still_judges()


# --- tampering with the output file -----------------------------------------------------------


def test_stdout_replaced_by_a_fifo_is_no_output_and_does_not_hang() -> None:
    started = time.monotonic()
    test = judge(f"{HOSTILE}/fifo_output.c")
    assert test["verdict"] == "WA", test
    assert time.monotonic() - started < 60
    assert_worker_still_judges()


@pytest.mark.parametrize("target", ["decoy", "/box/decoy", "/etc/shadow", "/proc/1/environ"])
def test_stdout_replaced_by_a_symlink_is_never_followed(target: str) -> None:
    source = f"{HOSTILE}/symlink_output.c"
    if target != "decoy":
        source = fixture_with(
            "symlink_output.c", '#define TARGET "decoy"', f'#define TARGET "{target}"'
        )
    # Following the link to the decoy (which holds the expected answer) would give AC.
    test = judge(source)
    assert test["verdict"] == "WA", test


def test_output_file_symlinked_to_the_input_is_not_accepted() -> None:
    # Control: a program that copies in.txt to out.txt is accepted (the answer is the input).
    echo = put_source(
        "hostile-file-echo.c",
        "#include <stdio.h>\n"
        "int main(void) {\n"
        '    FILE *in = fopen("in.txt", "r"), *out = fopen("out.txt", "w");\n'
        "    int c;\n"
        "    while ((c = fgetc(in)) != EOF) fputc(c, out);\n"
        "    return 0;\n"
        "}\n",
    )
    assert judge(echo, problem=FILE_IO_PROBLEM)["verdict"] == "AC"
    test = judge(f"{HOSTILE}/symlink_file_output.c", problem=FILE_IO_PROBLEM)
    assert test["verdict"] == "WA", test


# --- memory outside the heap ------------------------------------------------------------------


def test_600mb_of_memfd_and_shm_under_a_256mb_limit_is_stopped() -> None:
    # Uses the box's /dev/shm.
    test = judge(f"{HOSTILE}/memfd_shm.c")
    assert test["verdict"] in ("MLE", "RE"), test
    assert test["memory_kb"] <= MEMORY_LIMIT_KB, test
    # Nothing is left in isolate's folders or in the worker's own /dev/shm.
    shm = "/dev/shm"  # noqa: S108 - a path in the worker container, nothing is created there
    left = worker("find", ISOLATE_ROOT, shm, "-name", "hostile-shm-*", timeout=60)
    assert left.returncode == 0, left.stderr
    assert left.stdout.strip() == ""


# --- CPU pinning and resource limits ----------------------------------------------------------


def test_one_cpu_and_limits_that_cannot_be_raised() -> None:
    test = judge(f"{HOSTILE}/affinity.c")
    assert test["verdict"] == "AC", f"not contained: {test}"


# --- compile bombs ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "lang"),
    [
        ("compile_urandom.cpp", "cpp17"),  # reads the box's /dev/urandom
        ("compile_error_flood.cpp", "cpp17"),
        ("compile_huge_binary.c", "c11"),
    ],
)
def test_compile_bombs_are_compile_errors_with_a_capped_log(source: str, lang: str) -> None:
    started = time.monotonic()
    result = judge_all(APLUSB, f"{HOSTILE}/{source}", lang)
    elapsed = time.monotonic() - started
    assert result["verdict"] == "CE", {k: v for k, v in result.items() if k != "compile_log"}
    assert len(result["compile_log"].encode()) <= MAX_LOG_BYTES
    assert elapsed < 25 + 10  # the 20 s compile wall limit, plus starting the judge
