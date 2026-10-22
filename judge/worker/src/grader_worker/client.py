"""The worker's main loop: get jobs from web-admin's internal API, judge them, report back.

    judge-worker        (the worker container's command)

The worker polls: it asks for a job whenever a slot is free (every 2 s when there is nothing to
do), judges each job in its own thread, sends a heartbeat every 10 s for the jobs it holds (so
web-admin keeps their leases), posts each test's result as progress, and finally the result. If
web-admin is away it waits and tries again. A job whose lease web-admin gave to someone else
("lost") is dropped: its results would be refused anyway.

Settings: WEB_INTERNAL_URL (web-admin on judge_net), the token file `worker_token` in
WORKER_SECRETS_DIR (default /run/secrets/worker), WORKER_ID (default: the host name), and the
JUDGE_* settings of grader_worker.config. The worker never talks to anything else.
"""

import logging
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from grader_common import api
from grader_common.importer.errors import ProblemImportError
from grader_worker import config, health, selftest, tasks
from grader_worker.env_report import build_report
from grader_worker.isolate import IsolateError
from grader_worker.judge import MAX_MESSAGE_CHARS, SubmissionError, judge
from grader_worker.slots import SlotPool

logger = logging.getLogger("grader_worker.client")

MAX_REPLY_BYTES = 1 << 20  # a job carries at most 64 KB of source; anything bigger is wrong
_PROBLEM_PATH = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}/v[0-9]{3,}-[0-9a-f]{8}")


class ApiUnavailable(Exception):
    """web-admin couldn't be reached, or answered something we don't understand."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would re-send our headers, bearer token included, to
    whatever host the Location header names. A 3xx reply is then just an unexpected status."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class ApiClient:
    """The internal API's endpoints as methods. Every body is a grader_common.api model."""

    def __init__(self, base_url: str, token: str, timeout: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        # No proxies from the environment: web-admin is on judge_net, nowhere else.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects)

    def _post(self, path: str, body: BaseModel) -> tuple[int, bytes]:
        request = urllib.request.Request(  # noqa: S310 - our own fixed base URL
            self.base_url + path,
            data=body.model_dump_json().encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, response.read(MAX_REPLY_BYTES)
        except urllib.error.HTTPError as error:
            error.close()
            return error.code, b""
        except (urllib.error.URLError, OSError) as error:
            raise ApiUnavailable(f"{path}: {error}") from None

    def _expect(self, path: str, status: int, *ok: int) -> int:
        if status not in ok:
            raise ApiUnavailable(f"{path} answered {status}")
        return status

    def hello(self, worker_id: str, slots: int, env_report: dict[str, Any]) -> None:
        message = api.WorkerHello(worker_id=worker_id, slots=slots, env_report=env_report)
        status, _ = self._post("/internal/worker/hello", message)
        self._expect("hello", status, 204)

    def claim(self, worker_id: str, free_slots: int) -> api.JudgeJob | None:
        """One job, or None when there is nothing to do."""
        message = api.ClaimRequest(worker_id=worker_id, free_slots=free_slots)
        status, body = self._post("/internal/judge/claim", message)
        if self._expect("claim", status, 200, 204) == 204:
            return None
        try:
            return api.JudgeJob.model_validate_json(body)
        except ValidationError as error:
            raise ApiUnavailable(f"claim: not a valid job ({error.error_count()} errors)") from None

    def heartbeat(self, worker_id: str, job_ids: list[int]) -> list[int]:
        """Extend the leases; return the jobs we no longer hold."""
        message = api.Heartbeat(worker_id=worker_id, job_ids=job_ids)
        status, body = self._post("/internal/judge/heartbeat", message)
        self._expect("heartbeat", status, 200)
        try:
            return api.HeartbeatReply.model_validate_json(body).lost
        except ValidationError:
            raise ApiUnavailable("heartbeat: unexpected reply") from None

    def progress(self, worker_id: str, job_id: int, tests: list[api.TestResult]) -> bool:
        message = api.Progress(worker_id=worker_id, job_id=job_id, tests=tests)
        status, _ = self._post("/internal/judge/progress", message)
        return self._expect("progress", status, 204, 409) == 204

    def complete(self, worker_id: str, job_id: int, result: api.JudgeResult) -> bool:
        """Post the result. False: web-admin refused it (the job is no longer ours)."""
        message = api.Complete(worker_id=worker_id, job_id=job_id, result=result)
        status, _ = self._post("/internal/judge/complete", message)
        return self._expect("complete", status, 204, 409) == 204

    def report(
        self,
        worker_id: str,
        job_id: int,
        report: api.ValidationReport | api.CalibrationReport | api.SelfTestReport,
    ) -> bool:
        """Post the report of a job that isn't a submission. False: refused (no longer ours)."""
        message = api.Report(worker_id=worker_id, job_id=job_id, report=report)
        status, _ = self._post("/internal/judge/report", message)
        return self._expect("report", status, 204, 409) == 204

    def fail(self, worker_id: str, job_id: int, error: str) -> bool:
        message = api.Fail(worker_id=worker_id, job_id=job_id, error=error[:MAX_MESSAGE_CHARS])
        status, _ = self._post("/internal/judge/fail", message)
        return self._expect("fail", status, 204, 409) == 204


def resolve_problem(store: Path, problem_path: str) -> Path:
    """The version folder `problem_path` ("slug/vNNN-hash8") inside the store, checked.

    The job comes from web-admin, but the worker still refuses anything that isn't exactly two
    plain folder names below the store, with no symlink on the way.
    """
    if not _PROBLEM_PATH.fullmatch(problem_path):
        raise ProblemImportError(f"not a problem version path: {problem_path!r}")
    root = Path(os.path.realpath(store))
    folder = root.joinpath(*problem_path.split("/"))
    if os.path.realpath(folder) != str(folder) or not folder.is_dir():
        raise ProblemImportError(f"problem version {problem_path} is not in the store")
    return folder


def web_admin_ip(base_url: str) -> str:
    """web-admin's IPv4 address, for the self-test's network probe (the box has no DNS)."""
    host = urllib.parse.urlsplit(base_url).hostname or ""
    return socket.gethostbyname(host)


def default_worker_id() -> str:
    """The host name (the container's id under Docker), made safe for the API."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", socket.gethostname()).strip("-")
    return name[:64] or "worker"


class JudgeLoop:
    """Claim jobs while slots are free, judge them in threads, keep their leases, report back."""

    def __init__(
        self,
        *,
        api: ApiClient,
        pool: SlotPool,
        settings: config.Settings,
        worker_id: str,
        problems_dir: Path = config.PROBLEMS_DIR,
        judge_fn: Callable[..., api.JudgeResult] = judge,
        validate_fn: Callable[..., api.ValidationReport] = tasks.validate,
        calibrate_fn: Callable[..., api.CalibrationReport] = tasks.calibrate,
        selftest_fn: Callable[..., api.SelfTestReport] = selftest.run,
        fixtures_dir: Path = config.FIXTURES_DIR,
        env_report: Callable[[], dict[str, Any]] = dict,
        poll_seconds: float = 2,
        heartbeat_seconds: float = 10,
        hello_seconds: float = 600,
        retry_seconds: Sequence[float] = (1, 2, 4, 8, 16, 30),
    ) -> None:
        self.api = api
        self.pool = pool
        self.settings = settings
        self.worker_id = worker_id
        self.problems_dir = problems_dir
        self.judge_fn = judge_fn
        self.validate_fn = validate_fn
        self.calibrate_fn = calibrate_fn
        self.selftest_fn = selftest_fn
        self.fixtures_dir = fixtures_dir
        self.env_report = env_report
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.hello_seconds = hello_seconds
        self.retry_seconds = tuple(retry_seconds)
        self._running: set[int] = set()
        self._lost: set[int] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()  # set when a job finishes, to claim again at once

    def stop(self) -> None:
        """Stop claiming. Jobs already running finish in their threads (or their leases lapse)."""
        self._stop.set()
        self._wake.set()

    def run_forever(self) -> None:
        self._say_hello_until_it_works()
        threading.Thread(target=self._send_heartbeats, name="heartbeat", daemon=True).start()
        last_hello = time.monotonic()
        failures = 0
        while not self._stop.is_set():
            self._alive()
            if time.monotonic() - last_hello > self.hello_seconds:
                last_hello = time.monotonic()
                self._try_hello()
            free = self.settings.slots - self._running_count()
            if free > 0:
                try:
                    job = self.api.claim(self.worker_id, free_slots=free)
                except ApiUnavailable as error:
                    logger.warning("web-admin unavailable: %s", error)
                    self._stop.wait(self._backoff(failures))
                    failures += 1
                    continue
                failures = 0
                if job is not None:
                    self._start(job)
                    continue
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    # ---- helpers ----

    def _alive(self) -> None:
        """Tell the healthcheck the loop is still going (grader_worker.health)."""
        try:
            health.touch_alive(self.pool.state_dir)
        except OSError as error:
            logger.warning("can't write the alive file: %s", error)

    def _backoff(self, failures: int) -> float:
        return self.retry_seconds[min(failures, len(self.retry_seconds) - 1)]

    def _running_count(self) -> int:
        with self._lock:
            return len(self._running)

    def _try_hello(self) -> bool:
        try:
            self.api.hello(self.worker_id, self.settings.slots, self.env_report())
        except ApiUnavailable as error:
            logger.warning("hello failed: %s", error)
            return False
        return True

    def _say_hello_until_it_works(self) -> None:
        failures = 0
        while not self._stop.is_set() and not self._try_hello():
            self._alive()
            self._stop.wait(self._backoff(failures))
            failures += 1
        if not self._stop.is_set():
            logger.info("registered with web-admin")

    def _send_heartbeats(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            with self._lock:
                held = sorted(self._running - self._lost)
            if not held:
                continue
            try:
                lost = self.api.heartbeat(self.worker_id, held)
            except ApiUnavailable as error:
                logger.warning("heartbeat failed: %s", error)
                continue
            if lost:
                logger.warning("lost jobs (lease expired): %s", lost)
                with self._lock:
                    self._lost.update(lost)

    def _is_lost(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._lost

    def _start(self, job: api.JudgeJob) -> None:
        with self._lock:
            self._running.add(job.job_id)
        logger.info("job %s: %s in %s", job.job_id, job.problem_path, job.language)
        thread = threading.Thread(target=self._run, args=(job,), name=f"job-{job.job_id}")
        thread.daemon = True
        thread.start()

    def _run(self, job: api.JudgeJob) -> None:
        try:
            try:
                kind, outcome = self._work(job)
            except (SubmissionError, ProblemImportError, IsolateError) as error:
                self._report(job.job_id, "fail", str(error))
                return
            except Exception as error:  # a bug of ours: let web-admin retry it, then JE
                self._report(job.job_id, "fail", f"{type(error).__name__}: {error}")
                return
            self._report(job.job_id, kind, outcome)
        finally:
            with self._lock:
                self._running.discard(job.job_id)
                self._lost.discard(job.job_id)
            self._wake.set()

    def _work(self, job: api.JudgeJob) -> tuple[str, Any]:
        """Do the job in one slot; return how to answer ("complete" or "report") and with what."""
        if job.kind == "selftest" or job.problem_path is None:
            ip = web_admin_ip(self.api.base_url)
            with self.pool.acquire() as slot:
                return "report", self.selftest_fn(self.fixtures_dir, slot, self.judge_fn, ip)
        problem_dir = resolve_problem(self.problems_dir, job.problem_path)
        with self.pool.acquire() as slot:
            if job.kind == "validate":
                return "report", self.validate_fn(problem_dir, slot, job.time_scale, self.judge_fn)
            if job.kind == "calibrate":
                return "report", self.calibrate_fn(problem_dir, slot, job.time_scale, self.judge_fn)
            if job.language is None or job.source is None:  # api.JudgeJob already refuses this
                raise SubmissionError("a judge job without language or source")
            result = self.judge_fn(
                problem_dir,
                job.language,
                job.source.encode("utf-8"),
                slot,
                job.time_scale,
                on_test=lambda test: self._progress(job.job_id, test),
            )
            return "complete", result

    def _progress(self, job_id: int, test: api.TestResult) -> None:
        if self._is_lost(job_id):
            return
        try:
            self.api.progress(self.worker_id, job_id, [test])
        except ApiUnavailable as error:
            logger.warning("job %s: progress not sent: %s", job_id, error)

    def _report(self, job_id: int, kind: str, payload: Any) -> None:
        """Send the result (or failure), retrying while web-admin is away.

        The job stays in `_running` meanwhile, so heartbeats keep its lease. If every try fails,
        the lease lapses later and web-admin queues the job again.
        """
        senders: dict[str, Callable[[str, int, Any], bool]] = {
            "complete": self.api.complete,
            "report": self.api.report,
            "fail": self.api.fail,
        }
        send = senders[kind]
        for attempt in range(len(self.retry_seconds) + 1):
            if self._is_lost(job_id):
                logger.warning("job %s: lost, %s dropped", job_id, kind)
                return
            try:
                accepted = send(self.worker_id, job_id, payload)
            except ApiUnavailable as error:
                logger.warning("job %s: %s not sent (%s)", job_id, kind, error)
                if attempt < len(self.retry_seconds):
                    time.sleep(self.retry_seconds[attempt])
                continue
            if not accepted:
                logger.warning("job %s: web-admin refused the %s (no longer ours)", job_id, kind)
            return
        logger.error("job %s: giving up sending the %s", job_id, kind)


def judge_worker(argv: Sequence[str] | None = None) -> int:
    """`judge-worker`: run the loop until SIGTERM or SIGINT."""
    del argv  # no options
    logging.basicConfig(level=logging.INFO, format="worker: %(message)s", stream=sys.stdout)
    try:
        settings = config.Settings.from_env(os.environ)
        base_url = os.environ.get("WEB_INTERNAL_URL", "")
        if not base_url:
            raise config.ConfigError("WEB_INTERNAL_URL must be set, e.g. http://web-admin:8000")
        secrets_dir = Path(os.environ.get("WORKER_SECRETS_DIR", "/run/secrets/worker"))
        token = (secrets_dir / "worker_token").read_text(encoding="utf-8").strip()
        if not token:
            raise config.ConfigError(f"{secrets_dir}/worker_token is empty")
    except (config.ConfigError, OSError) as error:
        print(f"judge-worker: error: {error}", file=sys.stderr)
        return 2
    loop = JudgeLoop(
        api=ApiClient(base_url, token),
        pool=SlotPool(settings),
        settings=settings,
        worker_id=os.environ.get("WORKER_ID") or default_worker_id(),
        env_report=lambda: build_report(settings),
    )
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: loop.stop())
    logger.info("judging for %s as %s, %d slots", base_url, loop.worker_id, settings.slots)
    loop.run_forever()
    return 0
