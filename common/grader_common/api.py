"""Messages shared by the judge worker and the web app.

A judging result is produced by the worker and stored and shown by the web app, so its shape is
defined once here. So are the requests and replies of web-admin's internal judge API
(`/internal/*`): both sides validate every message with these models.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VerdictCode = Literal["AC", "PA", "WA", "TLE", "MLE", "RE", "OLE", "JE", "CE"]

# Upper bounds that no real result reaches. They keep impossible numbers out of the database
# (memory is stored in a 32-bit integer column).
MAX_SECONDS = 3600.0
MAX_MEMORY_KB = 2**31 - 1
# Scores are sums of floats, so "score <= max_score" allows for rounding.
SCORE_SLACK = 1e-6


class _Model(BaseModel):
    # allow_inf_nan=False: NaN or Infinity in a message is refused, never stored.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TestResult(_Model):
    """One test of one submission."""

    __test__ = False  # not a pytest test class, despite the name

    name: str = Field(min_length=1, max_length=64)
    verdict: VerdictCode
    outcome: float = Field(ge=0, le=1)
    time: float = Field(ge=0, le=MAX_SECONDS, description="CPU time in seconds")
    wall_time: float = Field(ge=0, le=MAX_SECONDS)
    memory_kb: int = Field(ge=0, le=MAX_MEMORY_KB)
    message: str = Field(default="", max_length=4096)


class SubtaskScore(_Model):
    name: str = Field(max_length=64)
    points: float = Field(ge=0)
    score: float = Field(ge=0)

    @model_validator(mode="after")
    def _score_within_points(self) -> "SubtaskScore":
        if self.score > self.points + SCORE_SLACK:
            raise ValueError("score is more than points")
        return self


class JudgeResult(_Model):
    """The whole result of judging one submission."""

    verdict: VerdictCode
    score: float = Field(ge=0)
    max_score: float = Field(ge=0)
    compile_log: str = Field(default="", max_length=64 * 1024)
    tests: list[TestResult] = Field(default=[], max_length=1000)
    subtasks: list[SubtaskScore] = Field(default=[], max_length=100)
    max_time: float = Field(default=0.0, ge=0, le=MAX_SECONDS)
    max_memory_kb: int = Field(default=0, ge=0, le=MAX_MEMORY_KB)
    box_id: int | None = None
    message: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def _score_within_max(self) -> "JudgeResult":
        if self.score > self.max_score + SCORE_SLACK:
            raise ValueError("score is more than max_score")
        return self


# ---- the internal judge API (the worker calls web-admin) ----

WorkerId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
JobId = Annotated[int, Field(ge=1)]
# A problem version folder, relative to the problem store: "<slug>/v<NNN>-<hash8>".
ProblemPath = Annotated[
    str, Field(max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}/v[0-9]{3,}-[0-9a-f]{8}$")
]


class WorkerHello(_Model):
    """POST /internal/worker/hello: the worker introduces itself (at start, then every 10 min)."""

    worker_id: WorkerId
    slots: int = Field(ge=1, le=10)
    env_report: dict[str, Any] = {}


class ClaimRequest(_Model):
    """POST /internal/judge/claim: ask for one job. The answer is a JudgeJob, or 204 for none."""

    worker_id: WorkerId
    free_slots: int = Field(ge=1, le=10)


JobKind = Literal["judge", "validate", "calibrate", "selftest"]


class JudgeJob(_Model):
    """One job for the worker. It carries everything needed, so the worker never reads the DB.

    `judge`: one submission (`language` and `source`), answered with Complete.
    `validate`: run a problem version's reference solutions, answered with Report.
    `calibrate`: time those solutions on the judge CPUs, answered with Report.
    `selftest`: run the hostile programs baked into the worker (no problem), answered with Report.
    Fields a kind doesn't use are left out (None), and must be.
    """

    job_id: JobId
    kind: JobKind
    problem_path: ProblemPath | None = None
    language: str | None = Field(default=None, max_length=16)
    source: str | None = Field(default=None, max_length=64 * 1024)
    time_scale: float = Field(gt=0, le=10)

    @model_validator(mode="after")
    def _fields_fit_the_kind(self) -> "JudgeJob":
        is_submission = self.language is not None and self.source is not None
        if (self.kind == "judge") != is_submission:
            raise ValueError("language and source go with judge jobs, and only with them")
        if self.language is None and self.source is not None:
            raise ValueError("source without language")
        if (self.kind == "selftest") != (self.problem_path is None):
            raise ValueError("every job but a self-test names a problem version")
        return self


class Heartbeat(_Model):
    """POST /internal/judge/heartbeat: "still working on these"; extends their leases."""

    worker_id: WorkerId
    job_ids: list[JobId] = Field(max_length=10)


class HeartbeatReply(_Model):
    """Jobs the worker no longer holds (lease expired and handed out again): drop their results."""

    lost: list[JobId]


class Progress(_Model):
    """POST /internal/judge/progress: results of the tests finished so far."""

    worker_id: WorkerId
    job_id: JobId
    tests: list[TestResult] = Field(max_length=1000)


class Complete(_Model):
    """POST /internal/judge/complete: the final result."""

    worker_id: WorkerId
    job_id: JobId
    result: JudgeResult


class SolutionRun(_Model):
    """How one reference solution did when its problem version was validated."""

    file: str = Field(max_length=200)
    lang: str = Field(max_length=16)
    expected_score: float | None = Field(default=None, ge=0)
    verdict: VerdictCode
    score: float = Field(ge=0)
    max_score: float = Field(ge=0)
    ok: bool
    message: str = Field(default="", max_length=4096)


class ValidationReport(_Model):
    """The answer to a `validate` job: every reference solution must be `ok`."""

    kind: Literal["validate"]
    solutions: list[SolutionRun] = Field(default=[], max_length=50)
    warnings: list[Annotated[str, Field(max_length=4096)]] = Field(default=[], max_length=20)

    @property
    def ok(self) -> bool:
        return all(run.ok for run in self.solutions)


class CalibrationTest(_Model):
    name: str = Field(min_length=1, max_length=64)
    max_time: float = Field(ge=0, le=MAX_SECONDS, description="the slowest CPU time over the runs")
    # False if any run got another verdict than AC here (say TLE): the time was cut off.
    accepted: bool = True


class CalibrationSolution(_Model):
    """One reference solution's times, per test, and its time limit at time scale 1.0."""

    file: str = Field(max_length=200)
    lang: str = Field(max_length=16)
    limit: float = Field(gt=0, le=MAX_SECONDS, description="seconds, with the language factor")
    tests: list[CalibrationTest] = Field(default=[], max_length=1000)
    verdicts: list[VerdictCode] = Field(default=[], max_length=10)


class CalibrationReport(_Model):
    """The answer to a `calibrate` job: each reference solution, run `runs` times."""

    kind: Literal["calibrate"]
    runs: int = Field(ge=1, le=10)
    solutions: list[CalibrationSolution] = Field(default=[], max_length=50)
    warnings: list[Annotated[str, Field(max_length=4096)]] = Field(default=[], max_length=20)


class SelfTestCase(_Model):
    """One hostile program of the self-test: the verdicts that mean "contained", and what it got."""

    name: str = Field(min_length=1, max_length=64)
    expected: list[VerdictCode] = Field(min_length=1, max_length=9)
    verdict: VerdictCode
    ok: bool
    seconds: float = Field(ge=0, le=MAX_SECONDS)
    message: str = Field(default="", max_length=4096)


class SelfTestReport(_Model):
    """The answer to a `selftest` job: every case must be `ok`."""

    kind: Literal["selftest"]
    cases: list[SelfTestCase] = Field(max_length=100)

    @property
    def ok(self) -> bool:
        return bool(self.cases) and all(case.ok for case in self.cases)


AnyReport = Annotated[
    ValidationReport | CalibrationReport | SelfTestReport, Field(discriminator="kind")
]


class Report(_Model):
    """POST /internal/judge/report: the answer to a job that isn't a submission."""

    worker_id: WorkerId
    job_id: JobId
    report: AnyReport


class Fail(_Model):
    """POST /internal/judge/fail: the worker couldn't judge the job (retried up to 3 tries)."""

    worker_id: WorkerId
    job_id: JobId
    error: str = Field(max_length=4096)
