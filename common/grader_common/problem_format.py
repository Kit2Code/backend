"""The problem format: what a `problem.yaml` may contain.

These pydantic models are the single definition of the format. The importer uses them to check a
problem before storing it, and the web app and the judge worker use them to read stored problems.
They only check what is inside the YAML file itself; whether the files it names really exist is
checked by the importer. The format is explained for humans in docs/PROBLEM_FORMAT.md, and
`python -m grader_common.problem_format` regenerates the JSON Schema next to this file.
"""

import json
import re
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SCHEMA_PATH = Path(__file__).parent / "schema" / "problem.schema.json"

Lang = Literal["th", "en"]
Language = Literal["c11", "cpp17", "cpp20", "python3"]

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# A test is the pair tests/<name>.in + tests/<name>.out.
# No leading dot, so "." and ".." are impossible.
TEST_NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")
_TEST_PATTERN_RE = re.compile(r"[A-Za-z0-9_.*?!\[\]-]{1,64}")
# Path parts never start with "-", so a file name can't be mistaken for a compiler option.
_PATH_PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_FILE_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
# The files the judge uses for redirected standard input, output and error inside a box.
STREAM_FILES = ("_stdin", "_stdout", "_stderr")
# A language's run-time factor may at most multiply the time limit by this.
MAX_LANG_TIME_FACTOR = 10


def _check_slug(value: str) -> str:
    if not _SLUG_RE.fullmatch(value):
        raise ValueError(
            "slug must be 1-64 of a-z, 0-9, '_' or '-', starting with a letter or digit"
        )
    return value


def _check_test_name(value: str) -> str:
    if not TEST_NAME_RE.fullmatch(value):
        raise ValueError(f"{value!r} is not a valid test name")
    return value


def _check_test_pattern(value: str) -> str:
    if not _TEST_PATTERN_RE.fullmatch(value):
        raise ValueError(f"{value!r} is not a valid test name pattern")
    return value


def _check_relative_path(value: str) -> str:
    """Accept only plain relative paths like `graders/grader.cpp`: no '..', no absolute paths."""
    parts = value.split("/")
    if not all(_PATH_PART_RE.fullmatch(part) for part in parts):
        raise ValueError(f"{value!r} must be a relative path of plain names separated by '/'")
    return value


def _check_io_file_name(value: str) -> str:
    if value and not _FILE_NAME_RE.fullmatch(value):
        raise ValueError(f"{value!r} must be empty (standard input/output) or a plain file name")
    return value


def _under(directory: str) -> AfterValidator:
    """A validator that requires a relative path to be inside `directory/`."""

    def check(value: str) -> str:
        if not value.startswith(directory + "/"):
            raise ValueError(f"{value!r} must be inside {directory}/")
        return value

    return AfterValidator(check)


Slug = Annotated[str, AfterValidator(_check_slug)]
TestName = Annotated[str, AfterValidator(_check_test_name)]
TestPattern = Annotated[str, AfterValidator(_check_test_pattern)]
RelativePath = Annotated[str, AfterValidator(_check_relative_path)]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=200)]


class _Model(BaseModel):
    """Base for every model here: unknown keys are errors, and loaded values can't be changed."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SumScoring(_Model):
    """CMS "Sum": every test is worth `total / number of tests`, times its outcome."""

    type: Literal["sum"]
    total: float = Field(gt=0)


class Subtask(_Model):
    """One CMS GroupMin subtask: `points` times the lowest outcome among its tests."""

    name: NonEmptyText
    points: float = Field(ge=0)
    tests: list[TestPattern] = Field(min_length=1)


class GroupMinScoring(_Model):
    """CMS "GroupMin": the score is the sum over subtasks."""

    type: Literal["group_min"]
    subtasks: list[Subtask] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [subtask.name for subtask in self.subtasks]
        if len(names) != len(set(names)):
            raise ValueError("subtask names must be unique")
        return self


Scoring = Annotated[SumScoring | GroupMinScoring, Field(discriminator="type")]


class Checker(_Model):
    """How outputs are compared: CMS white-diff, a CMS-style checker, or a testlib checker."""

    type: Literal["diff", "cms", "testlib"] = "diff"
    file: Annotated[RelativePath, _under("checker")] | None = None

    @model_validator(mode="after")
    def _file_matches_type(self) -> Self:
        if self.type == "diff" and self.file is not None:
            raise ValueError("a diff checker has no file")
        if self.type != "diff" and self.file is None:
            raise ValueError(f"a {self.type} checker needs `file`")
        return self


class InputOutput(_Model):
    """File names the program reads and writes. Empty means standard input / standard output."""

    input: Annotated[str, AfterValidator(_check_io_file_name)] = ""
    output: Annotated[str, AfterValidator(_check_io_file_name)] = ""


class Grader(_Model):
    """A CMS-style grader stub that is compiled together with the submission."""

    submission_file: Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\.%l$")]
    files: dict[Language, list[Annotated[RelativePath, _under("graders")]]] = Field(min_length=1)
    downloadable: list[Annotated[RelativePath, _under("graders")]] = []


class Solution(_Model):
    """A reference solution. Validation requires `expected_score` when it is given."""

    file: Annotated[RelativePath, _under("solutions")]
    lang: Language
    expected_score: float | None = Field(default=None, ge=0)


class Problem(_Model):
    """The whole `problem.yaml`."""

    format_version: Literal[1]
    slug: Slug
    title: dict[Lang, NonEmptyText] = Field(min_length=1)
    source: str = ""
    tags: list[NonEmptyText] = []
    type: Literal["batch"] = "batch"
    time_limit_ms: int = Field(ge=1, le=60_000)
    memory_limit_mb: int = Field(ge=16, le=512)
    languages: list[Language] = Field(min_length=1)
    lang_time_factor: dict[Language, Annotated[float, Field(gt=0, le=MAX_LANG_TIME_FACTOR)]] = {}
    io: InputOutput = InputOutput()
    scoring: Scoring
    samples: list[TestName] = []
    checker: Checker = Checker()
    grader: Grader | None = None
    solutions: list[Solution] = []
    visibility_default: Literal["hidden", "public"] = "hidden"
    license: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        allowed = set(self.languages)
        for solution in self.solutions:
            where = f"solution {solution.file}"
            if solution.lang not in allowed:
                raise ValueError(f"{where}: language {solution.lang} is not in languages")
            if solution.expected_score is not None and solution.expected_score > self.max_score():
                raise ValueError(f"{where}: expected_score is above the maximum score")
        if self.grader is not None:
            for lang in self.grader.files:
                if lang not in allowed:
                    raise ValueError(f"grader: language {lang} is not in languages")
        if len(self.samples) != len(set(self.samples)):
            raise ValueError("samples must be unique")
        self._check_io_names()
        return self

    def _check_io_names(self) -> None:
        """The program's input/output files must not overwrite the judge's own files in its box."""
        basename = self.executable_basename()
        # The executable is named after the submission; for Python it is a .zip archive.
        taken = {*STREAM_FILES, basename, basename + ".zip"}
        if self.io.input and self.io.input == self.io.output:
            raise ValueError("io: input and output must be different files")
        for name in (self.io.input, self.io.output):
            if name in taken:
                raise ValueError(f"io: {name!r} is a name the judge uses itself")

    def executable_basename(self) -> str:
        """The submission's file name without extension: from the grader, or "main"."""
        if self.grader is None:
            return "main"
        return self.grader.submission_file.removesuffix(".%l")

    def max_score(self) -> float:
        """The score of a fully correct submission."""
        if isinstance(self.scoring, SumScoring):
            return self.scoring.total
        return sum(subtask.points for subtask in self.scoring.subtasks)


def schema_json() -> str:
    """The JSON Schema of `problem.yaml`, as committed in SCHEMA_PATH."""
    schema = Problem.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(schema_json(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
