"""Command-line tools inside the worker container.

    judge-run --problem /data/problems/aplusb/v001-xxxxxxxx --lang cpp17 --source main.cpp

prints the judging result as JSON on stdout (exit 0).

    judge-run --jobs jobs.json        (or --jobs - to read stdin)

judges a JSON list of {"problem", "lang", "source", "time_scale"?} jobs at the same time, one per
free slot, and prints a JSON list of {"result", "box_id", "run_started", "run_finished"}.

An invalid request (unknown language, bad problem folder, bad job list) prints one error line on
stderr and exits 2.

    judge-selftest

judges the hostile programs (grader_worker.selftest) and prints the report; exit 1 if one of them
was not contained.
"""

import argparse
import json
import os
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grader_common.importer.errors import ProblemImportError
from grader_worker import config, selftest
from grader_worker.client import web_admin_ip
from grader_worker.env_report import judge_env as judge_env  # the `judge-env` command
from grader_worker.judge import MAX_SOURCE_BYTES, SubmissionError, judge
from grader_worker.slots import SlotPool

MAX_JOBS = 20


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: Path
    lang: str
    source: Path
    time_scale: float = Field(default=1.0, gt=0, le=10)


def judge_run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="judge-run", description="Judge source files.")
    parser.add_argument("--problem", type=Path, help="a problem version folder")
    parser.add_argument("--lang", help="c11, cpp17, cpp20 or python3")
    parser.add_argument("--source", type=Path, help="the source file")
    parser.add_argument("--time-scale", type=float, default=1.0, help="multiplies time limits")
    parser.add_argument("--jobs", help="a JSON file of jobs to judge at once ('-' for stdin)")
    args = parser.parse_args(argv)

    try:
        pool = SlotPool(config.Settings.from_env(os.environ))
        if args.jobs is not None:
            output: Any = _run_jobs(pool, _read_jobs(args.jobs))
        else:
            if args.problem is None or args.lang is None or args.source is None:
                raise SubmissionError("give --problem, --lang and --source (or --jobs)")
            job = Job(
                problem=args.problem, lang=args.lang, source=args.source, time_scale=args.time_scale
            )
            output = _run_one(pool, job)["result"]
    except (
        SubmissionError,
        ProblemImportError,
        ValidationError,
        config.ConfigError,
        OSError,
        KeyError,
        ValueError,
    ) as error:
        print(f"judge-run: error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2))
    return 0


def _read_jobs(where: str) -> list[Job]:
    text = sys.stdin.read() if where == "-" else Path(where).read_text()
    data = json.loads(text)
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_JOBS:
        raise ValueError(f"--jobs must be a JSON list of 1 to {MAX_JOBS} jobs")
    return [Job.model_validate(item) for item in data]


def _run_one(pool: SlotPool, job: Job) -> dict[str, Any]:
    with job.source.open("rb") as f:
        source = f.read(MAX_SOURCE_BYTES + 1)
    timeline: dict[str, float] = {}
    with pool.acquire() as slot:
        result = judge(job.problem, job.lang, source, slot, job.time_scale, timeline)
    return {
        "result": result.model_dump(mode="json"),
        "box_id": result.box_id,
        "run_started": timeline.get("run_started"),
        "run_finished": timeline.get("run_finished"),
    }


def _run_jobs(pool: SlotPool, jobs: list[Job]) -> list[dict[str, Any]]:
    """Judge the jobs in parallel threads; each waits for a slot and for memory as needed."""
    results: list[dict[str, Any] | None] = [None] * len(jobs)
    errors: list[BaseException] = []

    def work(index: int, job: Job) -> None:
        try:
            results[index] = _run_one(pool, job)
        except BaseException as error:  # reported after all threads finish
            errors.append(error)

    threads = [threading.Thread(target=work, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    return [r for r in results if r is not None]


def judge_selftest(argv: Sequence[str] | None = None) -> int:
    """`judge-selftest`: run the self-test in one slot, print the report; 1 if a case failed."""
    del argv  # no options
    try:
        pool = SlotPool(config.Settings.from_env(os.environ))
        ip = web_admin_ip(os.environ.get("WEB_INTERNAL_URL", "http://web-admin:8000"))
        with pool.acquire() as slot:
            report = selftest.run(config.FIXTURES_DIR, slot, judge, ip)
    except (SubmissionError, config.ConfigError, OSError) as error:
        print(f"judge-selftest: error: {error}", file=sys.stderr)
        return 2
    print(report.model_dump_json(indent=2))
    return 0 if report.ok else 1
