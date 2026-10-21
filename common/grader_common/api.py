"""Messages shared by the judge worker and the web app.

A judging result is produced by the worker (`judge-run`, and later the worker loop) and stored and
shown by the web app, so its shape is defined once here.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VerdictCode = Literal["AC", "PA", "WA", "TLE", "MLE", "RE", "OLE", "JE", "CE"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestResult(_Model):
    """One test of one submission."""

    __test__ = False  # not a pytest test class, despite the name

    name: str
    verdict: VerdictCode
    outcome: float = Field(ge=0, le=1)
    time: float = Field(ge=0, description="CPU time in seconds")
    wall_time: float = Field(ge=0)
    memory_kb: int = Field(ge=0)
    message: str = ""


class SubtaskScore(_Model):
    name: str
    points: float
    score: float


class JudgeResult(_Model):
    """The whole result of judging one submission."""

    verdict: VerdictCode
    score: float
    max_score: float
    compile_log: str = ""
    tests: list[TestResult] = []
    subtasks: list[SubtaskScore] = []
    max_time: float = 0.0
    max_memory_kb: int = 0
    box_id: int | None = None
    message: str = ""
