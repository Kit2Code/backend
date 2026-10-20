"""The problem.yaml models: what is accepted, what is refused, and the generated JSON Schema."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from grader_common import problem_format
from grader_common.problem_format import GroupMinScoring, Problem, SumScoring

EXAMPLE_YAML = """
format_version: 1
slug: aplusb
title: {th: "เอบวกบี", en: "A plus B"}
source: "Practice contest 2020, day 1"
tags: [math]
type: batch
time_limit_ms: 1000
memory_limit_mb: 256
languages: [c11, cpp17, cpp20, python3]
lang_time_factor: {python3: 1.0}
scoring:
  type: group_min
  subtasks:
    - {name: "1", points: 30, tests: ["01-*"]}
    - {name: "2", points: 70, tests: ["01-*", "02-*"]}
samples: ["00-sample-1"]
checker: {type: diff}
grader:
  submission_file: "aplusb.%l"
  files: {cpp17: [graders/grader.cpp, graders/aplusb.h], c11: [graders/grader.c, graders/aplusb.h]}
solutions:
  - {file: solutions/main.cpp, lang: cpp17, expected_score: 100}
  - {file: solutions/slow.py, lang: python3, expected_score: 30}
  - {file: solutions/try.py, lang: python3}
visibility_default: hidden
license: own
"""


def example() -> dict[str, Any]:
    data = yaml.safe_load(EXAMPLE_YAML)
    assert isinstance(data, dict)
    return data


def minimal() -> dict[str, Any]:
    return {
        "format_version": 1,
        "slug": "aplusb",
        "title": {"en": "A plus B"},
        "time_limit_ms": 1000,
        "memory_limit_mb": 256,
        "languages": ["cpp17"],
        "scoring": {"type": "sum", "total": 100},
    }


def test_example_from_the_docs_is_valid() -> None:
    problem = Problem.model_validate(example())
    assert problem.slug == "aplusb"
    assert problem.title["th"] == "เอบวกบี"
    assert isinstance(problem.scoring, GroupMinScoring)
    assert [s.points for s in problem.scoring.subtasks] == [30, 70]
    assert problem.grader is not None
    assert problem.grader.files["c11"][0] == "graders/grader.c"
    assert problem.solutions[2].expected_score is None


def test_minimal_problem_gets_defaults() -> None:
    problem = Problem.model_validate(minimal())
    assert isinstance(problem.scoring, SumScoring)
    assert problem.type == "batch"
    assert problem.checker.type == "diff"
    assert problem.checker.file is None
    assert problem.io.input == ""  # stdin
    assert problem.io.output == ""  # stdout
    assert problem.samples == []
    assert problem.solutions == []
    assert problem.grader is None
    assert problem.visibility_default == "hidden"


def test_models_are_frozen() -> None:
    problem = Problem.model_validate(minimal())
    with pytest.raises(ValidationError):
        problem.slug = "other"  # type: ignore[misc]


def _mutated(path: list[str | int], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(example())
    target: Any = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["format_version"], 2),
        (["slug"], "Has Space"),
        (["slug"], "-starts-with-dash"),
        (["slug"], "a" * 65),
        (["slug"], "../evil"),
        (["title"], {}),
        (["title"], {"fr": "Titre"}),
        (["title"], {"en": ""}),
        (["type"], "communication"),
        (["time_limit_ms"], 0),
        (["time_limit_ms"], 60_001),
        (["memory_limit_mb"], 513),
        (["memory_limit_mb"], 8),
        (["languages"], []),
        (["languages"], ["java"]),
        (["lang_time_factor"], {"python3": 0}),
        (["lang_time_factor"], {"rust": 1.0}),
        (["scoring", "type"], "group_mul"),
        (["scoring", "subtasks"], []),
        (["scoring", "subtasks", 0, "points"], -1),
        (["scoring", "subtasks", 0, "tests"], []),
        (["checker"], {"type": "cms"}),
        (["checker"], {"type": "testlib"}),
        (["checker"], {"type": "diff", "file": "checker/checker.cpp"}),
        (["checker", "file"], "/etc/passwd"),
        (["grader", "files"], {"java": ["graders/Grader.java"]}),
        (["grader", "files", "cpp17"], ["../outside.cpp"]),
        (["solutions", 0, "lang"], "pascal"),
        (["solutions", 0, "expected_score"], 101),
        (["solutions", 0, "file"], "solutions/../../x.cpp"),
        (["samples"], ["../tests/01"]),
        (["visibility_default"], "secret"),
        (["unknown_key"], 1),
    ],
)
def test_invalid_values_are_refused(path: list[str | int], value: Any) -> None:
    with pytest.raises(ValidationError):
        Problem.model_validate(_mutated(path, value))


def test_solution_language_must_be_allowed() -> None:
    data = minimal()
    data["solutions"] = [{"file": "solutions/a.py", "lang": "python3", "expected_score": 100}]
    with pytest.raises(ValidationError, match="python3"):
        Problem.model_validate(data)


def test_grader_language_must_be_allowed() -> None:
    data = minimal()
    data["grader"] = {"submission_file": "aplusb.%l", "files": {"c11": ["graders/grader.c"]}}
    with pytest.raises(ValidationError, match="c11"):
        Problem.model_validate(data)


def test_file_io_names_are_plain_file_names() -> None:
    data = minimal()
    data["io"] = {"input": "input.txt", "output": "output.txt"}
    assert Problem.model_validate(data).io.input == "input.txt"
    data["io"] = {"input": "../input.txt", "output": ""}
    with pytest.raises(ValidationError):
        Problem.model_validate(data)


def test_committed_json_schema_is_up_to_date() -> None:
    """Regenerate with: uv run python -m grader_common.problem_format"""
    committed = Path(problem_format.SCHEMA_PATH).read_text(encoding="utf-8")
    assert committed == problem_format.schema_json()
