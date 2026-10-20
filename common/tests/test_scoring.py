"""CMS scoring: Sum and GroupMin, with partial outcomes, missing results and bad inputs."""

import math

import pytest

from grader_common.problem_format import GroupMinScoring, Subtask, SumScoring
from grader_common.scoring import ScoreResult, resolve_subtasks, score, score_group_min, score_sum


def subtask(name: str, points: float, *tests: str) -> Subtask:
    return Subtask(name=name, points=points, tests=list(tests))


# --- resolving test patterns -------------------------------------------------------------


def test_tests_are_taken_in_lexicographic_order_like_cms() -> None:
    # CMS sorts test codenames as strings, so "10" comes before "2".
    resolved = resolve_subtasks(["2", "10", "1"], [subtask("all", 100, "*")])
    assert resolved == [["1", "10", "2"]]


def test_patterns_are_globs_and_their_union_has_no_duplicates() -> None:
    names = ["01-a", "01-b", "02-a", "02-b", "03-x"]
    resolved = resolve_subtasks(
        names,
        [subtask("1", 30, "01-*"), subtask("2", 70, "02-?", "01-*", "02-a")],
    )
    assert resolved == [["01-a", "01-b"], ["01-a", "01-b", "02-a", "02-b"]]


def test_character_classes_work() -> None:
    resolved = resolve_subtasks(["a1", "a2", "a3"], [subtask("s", 10, "a[12]")])
    assert resolved == [["a1", "a2"]]


def test_a_pattern_that_matches_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="03-\\*"):
        resolve_subtasks(["01-a"], [subtask("1", 50, "01-*"), subtask("2", 50, "03-*")])


# --- Sum ---------------------------------------------------------------------------------


def test_sum_all_correct_gives_the_total() -> None:
    tests = ["1", "2", "3"]
    assert score_sum(tests, {t: 1.0 for t in tests}, total=100) == pytest.approx(100)


def test_sum_each_test_is_worth_total_over_n_times_its_outcome() -> None:
    tests = ["1", "2", "3", "4"]
    outcomes = {"1": 1.0, "2": 0.5, "3": 0.0, "4": 0.25}
    assert score_sum(tests, outcomes, total=100) == pytest.approx(25 + 12.5 + 0 + 6.25)


def test_sum_missing_results_count_as_zero() -> None:
    assert score_sum(["1", "2"], {"1": 1.0}, total=10) == pytest.approx(5)


def test_sum_with_no_tests_is_zero() -> None:
    # CMS's loader sets the per-test value to 0 when there are no tests.
    assert score_sum([], {}, total=100) == 0


# --- GroupMin ----------------------------------------------------------------------------


def test_group_min_takes_the_lowest_outcome_of_each_subtask() -> None:
    subtasks = [subtask("1", 30, "1-*"), subtask("2", 70, "2-*")]
    resolved = [["1-a", "1-b"], ["2-a", "2-b"]]
    outcomes = {"1-a": 1.0, "1-b": 1.0, "2-a": 1.0, "2-b": 0.5}
    result = score_group_min(subtasks, resolved, outcomes)
    assert result.score == pytest.approx(30 + 35)
    assert [s.score for s in result.subtasks] == pytest.approx([30, 35])
    assert [s.name for s in result.subtasks] == ["1", "2"]


def test_group_min_one_wrong_test_zeroes_its_subtask_only() -> None:
    subtasks = [subtask("1", 40, "a"), subtask("2", 60, "b")]
    result = score_group_min(subtasks, [["a"], ["a", "b"]], {"a": 0.0, "b": 1.0})
    assert result.score == 0
    result = score_group_min(subtasks, [["a"], ["b"]], {"a": 0.0, "b": 1.0})
    assert result.score == pytest.approx(60)


def test_group_min_zero_point_subtask_is_allowed() -> None:
    subtasks = [subtask("samples", 0, "0*"), subtask("all", 100, "*")]
    result = score_group_min(subtasks, [["00"], ["00", "01"]], {"00": 1.0, "01": 1.0})
    assert result.score == pytest.approx(100)
    assert result.subtasks[0].score == 0


def test_group_min_missing_result_counts_as_zero() -> None:
    result = score_group_min([subtask("1", 100, "*")], [["a", "b"]], {"a": 1.0})
    assert result.score == 0


def test_group_min_empty_subtask_is_an_error() -> None:
    # CMS would crash on min([]) while judging; we refuse it instead of guessing a score.
    with pytest.raises(ValueError, match="no tests"):
        score_group_min([subtask("1", 100, "*")], [[]], {})


def test_group_min_needs_one_test_list_per_subtask() -> None:
    with pytest.raises(ValueError):
        score_group_min([subtask("1", 50, "a"), subtask("2", 50, "b")], [["a"]], {"a": 1.0})


# --- outcomes must be in [0, 1] ----------------------------------------------------------


@pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan, math.inf])
def test_outcomes_outside_zero_to_one_are_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="outcome"):
        score_sum(["a"], {"a": bad}, total=100)
    with pytest.raises(ValueError, match="outcome"):
        score_group_min([subtask("1", 100, "a")], [["a"]], {"a": bad})


# --- the one entry point the worker will call --------------------------------------------


def test_score_dispatches_on_the_scoring_type() -> None:
    tests = ["01-a", "01-b", "02-a"]
    outcomes = {"01-a": 1.0, "01-b": 1.0, "02-a": 0.0}

    as_sum = score(SumScoring(type="sum", total=90), tests, outcomes)
    assert isinstance(as_sum, ScoreResult)
    assert as_sum.score == pytest.approx(60)
    assert as_sum.subtasks == []

    as_group = score(
        GroupMinScoring(
            type="group_min",
            subtasks=[subtask("1", 30, "01-*"), subtask("2", 70, "*")],
        ),
        tests,
        outcomes,
    )
    assert as_group.score == pytest.approx(30)
    assert as_group.subtasks[1].tests == ["01-a", "01-b", "02-a"]
