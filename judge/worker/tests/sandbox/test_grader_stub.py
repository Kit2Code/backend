"""Batch tasks with a grader stub, in C++ and C (real worker container).

Like CMS (cms/grading/tasktypes/Batch.py): the submission is saved as `submission_file` with the
language's extension, compiled together with the grader (grader first) and the headers.
"""

import pytest
from conftest import FIXTURES_IN_WORKER, judge, put_source, worker

PROBLEM = f"{FIXTURES_IN_WORKER}/problems/grader-stub"


@pytest.mark.parametrize(
    ("lang", "solution"),
    [("cpp17", "adder.cpp"), ("cpp20", "adder.cpp"), ("c11", "adder.c")],
)
def test_reference_solutions_pass_with_the_grader(lang: str, solution: str) -> None:
    result = judge(PROBLEM, lang, f"{PROBLEM}/solutions/{solution}")
    assert result["verdict"] == "AC", result
    assert result["score"] == 100


def test_the_header_is_available_to_the_submission() -> None:
    # adder.cpp includes "adder.h"; without the header in the box this would not compile.
    source = put_source(
        "uses_header.cpp",
        '#include "adder.h"\nlong long add(long long a, long long b) { return a + b; }\n',
    )
    assert judge(PROBLEM, "cpp17", source)["verdict"] == "AC"


def test_a_submission_with_its_own_main_does_not_link() -> None:
    source = put_source(
        "own_main.cpp",
        "long long add(long long a, long long b) { return a + b; }\nint main() { return 0; }\n",
    )
    result = judge(PROBLEM, "cpp17", source)
    assert result["verdict"] == "CE"
    assert "main" in result["compile_log"]


def test_a_wrong_function_is_wrong_answer() -> None:
    source = put_source(
        "wrong_add.c",
        '#include "adder.h"\nlong long add(long long a, long long b) { return a - b; }\n',
    )
    result = judge(PROBLEM, "c11", source)
    assert result["verdict"] == "WA"


def test_languages_without_a_grader_are_refused() -> None:
    source = put_source("adder.py", "def add(a, b):\n    return a + b\n")
    result = worker("judge-run", "--problem", PROBLEM, "--lang", "python3", "--source", source)
    assert result.returncode == 2
    assert "not allowed" in result.stderr
