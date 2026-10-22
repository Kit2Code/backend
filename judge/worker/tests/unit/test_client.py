"""The worker's client loop against a fake web-admin (a tiny HTTP server in the test).

The real judge is replaced by a fake one, so no isolate is needed; the sandbox tests cover
judging itself. What is checked here: the HTTP calls, leases and heartbeats, progress, results,
failures, lost jobs, slot limits, polling and retrying.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grader_common.api import JudgeResult, TestResult
from grader_common.importer.errors import ProblemImportError
from grader_worker import client as client_module
from grader_worker.client import ApiClient, ApiUnavailable, JudgeLoop, resolve_problem
from grader_worker.config import Settings
from grader_worker.judge import SubmissionError, judge
from grader_worker.slots import SlotPool

TOKEN = "test-token-123"


def job(job_id: int, path: str = "aplusb/v001-0123abcd") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "kind": "judge",
        "problem_path": path,
        "language": "cpp17",
        "source": "int main(){}",
        "time_scale": 1.0,
    }


def ac_result() -> JudgeResult:
    return JudgeResult(verdict="AC", score=100, max_score=100)


def a_test(name: str) -> TestResult:
    return TestResult(name=name, verdict="AC", outcome=1, time=0.01, wall_time=0.02, memory_kb=1)


@dataclass
class FakeWeb:
    """web-admin's internal API, in memory. Records every call."""

    jobs: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    lost: list[int] = field(default_factory=list)
    complete_status: int = 204
    lock: threading.Lock = field(default_factory=threading.Lock)

    def handle(
        self, path: str, body: dict[str, Any], auth: str
    ) -> tuple[int, dict[str, Any] | None]:
        with self.lock:
            self.calls.append((path, body, auth))
            if path == "/internal/judge/claim":
                return (200, self.jobs.pop(0)) if self.jobs else (204, None)
            if path == "/internal/judge/heartbeat":
                return 200, {"lost": [i for i in body["job_ids"] if i in self.lost]}
            if path == "/internal/judge/complete":
                return self.complete_status, None
            return 204, None

    def bodies(self, path: str) -> list[dict[str, Any]]:
        with self.lock:
            return [body for p, body, _ in self.calls if p == path]


@pytest.fixture
def web() -> Iterator[tuple[FakeWeb, str]]:
    fake = FakeWeb()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            status, reply = fake.handle(self.path, body, self.headers.get("Authorization", ""))
            data = json.dumps(reply).encode() if reply is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def store(tmp_path: Path) -> Path:
    folder = tmp_path / "problems"
    (folder / "aplusb" / "v001-0123abcd").mkdir(parents=True)
    return folder


def make_loop(
    url: str,
    store: Path,
    tmp_path: Path,
    judge_fn: Callable[..., JudgeResult],
    slots: int = 2,
) -> JudgeLoop:
    settings = Settings(slots=slots, box_cpus=list(range(slots)), mem_total=1 << 30)
    return JudgeLoop(
        api=ApiClient(url, TOKEN, timeout=5),
        pool=SlotPool(settings, state_dir=tmp_path / "state"),
        settings=settings,
        worker_id="test-worker",
        problems_dir=store,
        judge_fn=judge_fn,
        env_report=lambda: {"isolate": "2.7"},
        poll_seconds=0.05,
        heartbeat_seconds=0.05,
        hello_seconds=3600,
        retry_seconds=(0.01, 0.02),
    )


def run_until(loop: JudgeLoop, done: Callable[[], bool], timeout: float = 10) -> None:
    """Run the loop in a thread until `done()` is true, then stop it."""
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)
    loop.stop()
    thread.join(timeout=10)
    assert not thread.is_alive()


# ---- the HTTP client ----


def test_client_sends_the_token_and_json(web: tuple[FakeWeb, str]) -> None:
    fake, url = web
    fake.jobs.append(job(7))
    api = ApiClient(url, TOKEN, timeout=5)
    claimed = api.claim("w", free_slots=1)
    assert claimed is not None and claimed.job_id == 7
    path, body, auth = fake.calls[0]
    assert path == "/internal/judge/claim"
    assert body == {"worker_id": "w", "free_slots": 1}
    assert auth == f"Bearer {TOKEN}"
    assert api.claim("w", free_slots=1) is None  # 204


def test_client_reports_409_as_not_accepted(web: tuple[FakeWeb, str]) -> None:
    fake, url = web
    fake.complete_status = 409
    assert ApiClient(url, TOKEN).complete("w", 1, ac_result()) is False


def test_client_raises_when_web_admin_is_unreachable() -> None:
    with pytest.raises(ApiUnavailable):
        ApiClient("http://127.0.0.1:9", TOKEN, timeout=1).claim("w", free_slots=1)


def test_client_refuses_a_bad_job(web: tuple[FakeWeb, str]) -> None:
    fake, url = web
    fake.jobs.append({**job(1), "problem_path": "../../etc"})
    with pytest.raises(ApiUnavailable):
        ApiClient(url, TOKEN).claim("w", free_slots=1)


# ---- finding the problem ----


def test_resolve_problem(store: Path) -> None:
    assert resolve_problem(store, "aplusb/v001-0123abcd") == store / "aplusb" / "v001-0123abcd"


@pytest.mark.parametrize(
    "path", ["aplusb/v002-0123abcd", "../x/v001-0123abcd", "aplusb", "aplusb/v001-0123abcd/tests"]
)
def test_resolve_problem_refuses(store: Path, path: str) -> None:
    with pytest.raises(ProblemImportError):
        resolve_problem(store, path)


def test_resolve_problem_refuses_a_symlink_out_of_the_store(store: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "v001-0123abcd"
    outside.mkdir(parents=True)
    (store / "evil").symlink_to(tmp_path / "outside")
    with pytest.raises(ProblemImportError):
        resolve_problem(store, "evil/v001-0123abcd")


# ---- the loop ----


def test_a_job_is_judged_and_reported(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path
) -> None:
    fake, url = web
    fake.jobs.append(job(1))
    seen: dict[str, Any] = {}

    def fake_judge(problem_dir: Path, lang: str, source: bytes, slot: Any, time_scale: float,
                   timeline: Any = None, on_test: Any = None) -> JudgeResult:  # fmt: skip
        seen.update(problem_dir=problem_dir, lang=lang, source=source, box=slot.box_id)
        on_test(a_test("01"))
        on_test(a_test("02"))
        return ac_result()

    loop = make_loop(url, store, tmp_path, fake_judge)
    run_until(loop, lambda: bool(fake.bodies("/internal/judge/complete")))
    assert seen == {
        "problem_dir": store / "aplusb" / "v001-0123abcd",
        "lang": "cpp17",
        "source": b"int main(){}",
        "box": 100,
    }
    hello = fake.bodies("/internal/worker/hello")[0]
    assert hello == {"worker_id": "test-worker", "slots": 2, "env_report": {"isolate": "2.7"}}
    progress = fake.bodies("/internal/judge/progress")
    assert [p["tests"][0]["name"] for p in progress] == ["01", "02"]
    (done,) = fake.bodies("/internal/judge/complete")
    assert done["job_id"] == 1 and done["result"]["verdict"] == "AC"
    assert not fake.bodies("/internal/judge/fail")


@pytest.mark.parametrize(
    "error",
    [SubmissionError("language 'x' is not allowed"), ProblemImportError("broken problem.yaml")],
)
def test_a_job_that_cannot_be_judged_is_reported_as_failed(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path, error: Exception
) -> None:
    fake, url = web
    fake.jobs.append(job(1))

    def fake_judge(*args: Any, **kwargs: Any) -> JudgeResult:
        raise error

    run_until(
        make_loop(url, store, tmp_path, fake_judge),
        lambda: bool(fake.bodies("/internal/judge/fail")),
    )
    (failed,) = fake.bodies("/internal/judge/fail")
    assert failed["job_id"] == 1
    assert str(error) in failed["error"]
    assert not fake.bodies("/internal/judge/complete")


def test_an_unexpected_error_is_reported_as_failed(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path
) -> None:
    fake, url = web
    fake.jobs.append(job(1))

    def fake_judge(*args: Any, **kwargs: Any) -> JudgeResult:
        raise RuntimeError("boom")

    run_until(
        make_loop(url, store, tmp_path, fake_judge),
        lambda: bool(fake.bodies("/internal/judge/fail")),
    )
    assert "RuntimeError" in fake.bodies("/internal/judge/fail")[0]["error"]


def test_a_missing_problem_folder_fails_the_job(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path
) -> None:
    fake, url = web
    fake.jobs.append(job(1, path="aplusb/v009-0123abcd"))
    called: list[int] = []

    def counting_judge(*args: Any, **kwargs: Any) -> JudgeResult:
        called.append(1)
        return ac_result()

    loop = make_loop(url, store, tmp_path, counting_judge)
    run_until(loop, lambda: bool(fake.bodies("/internal/judge/fail")))
    assert called == []


def test_heartbeats_while_judging_and_lost_jobs_are_dropped(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path
) -> None:
    fake, url = web
    fake.jobs.append(job(5))
    fake.lost = [5]
    release = threading.Event()

    def slow_judge(*args: Any, on_test: Any = None, **kwargs: Any) -> JudgeResult:
        release.wait(10)
        on_test(a_test("01"))
        return ac_result()

    loop = make_loop(url, store, tmp_path, slow_judge)
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not fake.bodies("/internal/judge/heartbeat"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert fake.bodies("/internal/judge/heartbeat")[0]["job_ids"] == [5]
    release.set()
    time.sleep(0.5)
    loop.stop()
    thread.join(timeout=10)
    # The lease went to someone else: no progress or result for it from us.
    assert not fake.bodies("/internal/judge/complete")
    assert not fake.bodies("/internal/judge/progress")


def test_never_more_jobs_than_slots(web: tuple[FakeWeb, str], store: Path, tmp_path: Path) -> None:
    fake, url = web
    fake.jobs.extend(job(i) for i in range(1, 6))
    running = 0
    most = 0
    lock = threading.Lock()

    def counting_judge(*args: Any, **kwargs: Any) -> JudgeResult:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.1)
        with lock:
            running -= 1
        return ac_result()

    loop = make_loop(url, store, tmp_path, counting_judge, slots=2)
    run_until(loop, lambda: len(fake.bodies("/internal/judge/complete")) == 5)
    assert most == 2
    for claim in fake.bodies("/internal/judge/claim"):
        assert 1 <= claim["free_slots"] <= 2


def test_idle_worker_keeps_polling(web: tuple[FakeWeb, str], store: Path, tmp_path: Path) -> None:
    fake, url = web
    loop = make_loop(url, store, tmp_path, lambda *a, **k: ac_result())
    run_until(loop, lambda: len(fake.bodies("/internal/judge/claim")) >= 5)
    fake.jobs.append(job(9))
    run_until(make_loop(url, store, tmp_path, lambda *a, **k: ac_result()),
              lambda: bool(fake.bodies("/internal/judge/complete")))  # fmt: skip


def test_retries_until_web_admin_answers(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, url = web
    fake.jobs.append(job(1))
    real_post = ApiClient._post
    failures = {"left": 3}

    def flaky_post(self: ApiClient, path: str, body: Any) -> tuple[int, bytes]:
        if failures["left"] > 0:
            failures["left"] -= 1
            raise ApiUnavailable("connection refused")
        return real_post(self, path, body)

    monkeypatch.setattr(ApiClient, "_post", flaky_post)
    loop = make_loop(url, store, tmp_path, lambda *a, **k: ac_result())
    run_until(loop, lambda: bool(fake.bodies("/internal/judge/complete")))
    assert failures["left"] == 0


def test_the_result_is_retried_if_web_admin_is_briefly_away(
    web: tuple[FakeWeb, str], store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, url = web
    fake.jobs.append(job(1))
    real_complete = ApiClient.complete
    failures = {"left": 2}

    def flaky_complete(self: ApiClient, *args: Any) -> bool:
        if failures["left"] > 0:
            failures["left"] -= 1
            raise ApiUnavailable("connection reset")
        return real_complete(self, *args)

    monkeypatch.setattr(ApiClient, "complete", flaky_complete)
    loop = make_loop(url, store, tmp_path, lambda *a, **k: ac_result())
    run_until(loop, lambda: bool(fake.bodies("/internal/judge/complete")))


def test_worker_id_from_the_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("socket.gethostname", lambda: "box/1 evil")
    assert client_module.default_worker_id() == "box-1-evil"


# ---- judge() reports each test as it finishes ----


def test_judge_calls_on_test_for_every_test() -> None:
    from test_judge_errors import PROBLEM, SOURCE, FakeSlot

    seen: list[str] = []
    result = judge(
        PROBLEM,
        "c11",
        SOURCE,
        FakeSlot(),  # type: ignore[arg-type]
        on_test=lambda t: seen.append(t.name),
    )
    assert seen == [t.name for t in result.tests]
    assert seen


def test_a_failing_progress_callback_does_not_break_judging() -> None:
    from test_judge_errors import PROBLEM, SOURCE, FakeSlot

    def broken(test: TestResult) -> None:
        raise ApiUnavailable("web-admin is away")

    result = judge(PROBLEM, "c11", SOURCE, FakeSlot(), on_test=broken)  # type: ignore[arg-type]
    assert result.tests


def test_client_never_follows_a_redirect() -> None:
    """A redirect would carry the bearer token to whatever host the Location header names."""
    seen_auth: list[str] = []

    class Elsewhere(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen_auth.append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    elsewhere = ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)
    target = f"http://127.0.0.1:{elsewhere.server_address[1]}/steal"

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    redirector = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
    servers = (elsewhere, redirector)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{redirector.server_address[1]}"
        with pytest.raises(ApiUnavailable):
            ApiClient(url, TOKEN, timeout=5).claim("w", free_slots=1)
        assert seen_auth == []
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
