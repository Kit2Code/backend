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


class JudgeJob(_Model):
    """One submission to judge. It carries everything needed, so the worker never reads the DB."""

    job_id: JobId
    kind: Literal["judge"]
    problem_path: ProblemPath
    language: str = Field(max_length=16)
    source: str = Field(max_length=64 * 1024)
    time_scale: float = Field(gt=0, le=10)


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


class Fail(_Model):
    """POST /internal/judge/fail: the worker couldn't judge the job (retried up to 3 tries)."""

    worker_id: WorkerId
    job_id: JobId
    error: str = Field(max_length=4096)
