"""Compile and run commands are exactly CMS v1.5.1's (cms/grading/languages/*.py)."""

import pytest

from grader_worker.languages import LANGUAGES


def test_the_four_languages() -> None:
    assert list(LANGUAGES) == ["c11", "cpp17", "cpp20", "python3"]


def test_c11() -> None:
    c = LANGUAGES["c11"]
    assert c.source_extension == ".c"
    assert c.compile_commands(["grader.c", "adder.c"], "adder") == [
        ["/usr/bin/gcc", "-DEVAL", "-std=gnu11", "-O2", "-pipe", "-static", "-s", "-o", "adder",
         "grader.c", "adder.c", "-lm"],
    ]  # fmt: skip
    assert c.run_command("adder") == ["./adder"]


@pytest.mark.parametrize(("key", "std"), [("cpp17", "gnu++17"), ("cpp20", "gnu++20")])
def test_cpp(key: str, std: str) -> None:
    cpp = LANGUAGES[key]
    assert cpp.source_extension == ".cpp"
    assert cpp.compile_commands(["main.cpp"], "main") == [
        ["/usr/bin/g++", "-DEVAL", f"-std={std}", "-O2", "-pipe", "-static", "-s", "-o", "main",
         "main.cpp"],
    ]  # fmt: skip
    assert cpp.run_command("main") == ["./main"]


def test_python3() -> None:
    python = LANGUAGES["python3"]
    assert python.source_extension == ".py"
    assert python.executable_name("main") == "main.zip"
    assert python.compile_commands(["main.py", "helper.py"], "main.zip") == [
        ["/usr/bin/python3", "-m", "compileall", "-b", "."],
        ["/bin/mv", "main.pyc", "__main__.pyc"],
        ["/usr/bin/zip", "main.zip", "__main__.pyc", "helper.pyc"],
    ]
    assert python.run_command("main.zip") == ["/usr/bin/python3", "main.zip"]


def test_compiled_languages_have_no_executable_extension() -> None:
    assert LANGUAGES["cpp17"].executable_name("main") == "main"
