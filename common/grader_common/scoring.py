"""Scores from per-test outcomes, the way CMS v1.5.1 computes them.

An *outcome* is a number from 0 (wrong) to 1 (correct) that the checker gives each test; values in
between are partial credit. Rules re-implemented from CMS v1.5.1:

- Sum (`cms/grading/scoretypes/Sum.py`): each test is worth the same share of the total, times its
  outcome.
- GroupMin (`cms/grading/scoretypes/GroupMin.py` and `ScoreTypeGroup` in `abc.py`): each subtask
  scores its points times the lowest outcome among its tests.
- Tests are taken in lexicographic order of their names (`abc.py`, `retrieve_target_testcases`).

A test with no outcome (for example because the submission didn't compile) counts as 0. Nothing is
rounded here; rounding is only for display.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from grader_common.problem_format import Scoring, Subtask, SumScoring


@dataclass(frozen=True)
class SubtaskResult:
    """The score of one subtask and the tests it was computed from."""

    name: str
    points: float
    score: float
    tests: list[str]


@dataclass(frozen=True)
class ScoreResult:
    """The total score, plus one entry per subtask (empty for Sum scoring)."""

    score: float
    subtasks: list[SubtaskResult]


def resolve_subtasks(test_names: Sequence[str], subtasks: Sequence[Subtask]) -> list[list[str]]:
    """Turn each subtask's name patterns into its list of tests, in CMS order.

    Patterns are shell-style globs (`01-*`, `0?`, `a[12]`). A pattern that matches no test is an
    error, because it almost always means a typo in problem.yaml.
    """
    ordered = sorted(test_names)
    resolved = []
    for subtask in subtasks:
        for pattern in subtask.tests:
            if not any(fnmatchcase(name, pattern) for name in ordered):
                raise ValueError(f"subtask {subtask.name}: pattern {pattern!r} matches no test")
        resolved.append(
            [name for name in ordered if any(fnmatchcase(name, p) for p in subtask.tests)]
        )
    return resolved


def score_sum(test_names: Sequence[str], outcomes: Mapping[str, float], total: float) -> float:
    """CMS Sum: every test is worth `total / number of tests`, times its outcome."""
    if not test_names:
        return 0.0
    per_test = total / len(test_names)
    return sum(_outcome(outcomes, name) * per_test for name in test_names)


def score_group_min(
    subtasks: Sequence[Subtask],
    resolved: Sequence[Sequence[str]],
    outcomes: Mapping[str, float],
) -> ScoreResult:
    """CMS GroupMin: each subtask scores its points times its lowest test outcome.

    `resolved` holds the tests of each subtask, as returned by `resolve_subtasks`.
    """
    if len(subtasks) != len(resolved):
        raise ValueError("need exactly one list of tests per subtask")
    results = []
    for subtask, tests in zip(subtasks, resolved, strict=True):
        if not tests:
            raise ValueError(f"subtask {subtask.name} has no tests")
        lowest = min(_outcome(outcomes, name) for name in tests)
        results.append(
            SubtaskResult(
                name=subtask.name,
                points=subtask.points,
                score=subtask.points * lowest,
                tests=list(tests),
            )
        )
    return ScoreResult(score=sum(r.score for r in results), subtasks=results)


def score(
    scoring: Scoring, test_names: Sequence[str], outcomes: Mapping[str, float]
) -> ScoreResult:
    """Score a submission with the problem's scoring rule."""
    if isinstance(scoring, SumScoring):
        return ScoreResult(score=score_sum(test_names, outcomes, scoring.total), subtasks=[])
    resolved = resolve_subtasks(test_names, scoring.subtasks)
    return score_group_min(scoring.subtasks, resolved, outcomes)


def _outcome(outcomes: Mapping[str, float], name: str) -> float:
    """The outcome of one test: 0 if it has none, and an error if it isn't in [0, 1]."""
    value = outcomes.get(name, 0.0)
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise ValueError(f"test {name}: outcome {value!r} is not between 0 and 1")
    return value
