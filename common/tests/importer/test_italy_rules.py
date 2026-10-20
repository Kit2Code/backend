"""CMS Italian-format rules (cmscontrib/loaders/italy_yaml.py at CMS v1.5.1), one rule per test."""

from pathlib import Path

import pytest
import yaml

from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import ImportResult, import_problem
from grader_common.problem_format import GroupMinScoring, SumScoring

PDF = b"%PDF-1.4\n%fake statement for tests\n"


def make_task(
    root: Path,
    conf: dict[str, object] | None = None,
    *,
    n_tests: int = 3,
    gen: str | None = None,
    files: dict[str, bytes | str] | None = None,
    statement: dict[str, bytes] | None = None,
) -> Path:
    """Write a small Italian-format task folder. Defaults: Sum over 3 tests, no GEN."""
    task = {"name": "somma", "title": "Somma", "time_limit": 1, "memory_limit": 256}
    if gen is None and "n_input" not in (conf or {}):
        task["n_input"] = n_tests
    task.update(conf or {})
    root.mkdir(parents=True)
    (root / "task.yaml").write_text(yaml.safe_dump(task, allow_unicode=True))
    for i in range(n_tests):
        for kind in ("input", "output"):
            path = root / kind / f"{kind}{i}.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"{kind} {i}\n")
    if gen is not None:
        (root / "gen").mkdir()
        (root / "gen" / "GEN").write_text(gen)
    for name, data in (
        statement if statement is not None else {"statement/statement.pdf": PDF}
    ).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name, content in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    return root


def run(tmp_path: Path, **kwargs: object) -> ImportResult:
    task = make_task(tmp_path / "task", **kwargs)  # type: ignore[arg-type]
    return import_problem(task, tmp_path / "store")


def refused(tmp_path: Path, match: str, **kwargs: object) -> None:
    task = make_task(tmp_path / "task", **kwargs)  # type: ignore[arg-type]
    store_dir = tmp_path / "store"
    with pytest.raises(ProblemImportError, match=match):
        import_problem(task, store_dir)
    assert not (store_dir / "somma").exists()


def stored_yaml(result: ImportResult) -> dict[str, object]:
    data = yaml.safe_load((result.version_dir / "problem.yaml").read_text())
    assert isinstance(data, dict)
    return data


# --- names, limits, files ----------------------------------------------------------------


def test_basic_task_maps_names_limits_and_tests(tmp_path: Path) -> None:
    result = run(tmp_path, conf={"time_limit": 1.5, "memory_limit": 128})
    problem = result.problem
    assert problem.slug == "somma"
    assert problem.title == {"th": "Somma"}
    assert problem.time_limit_ms == 1500
    assert problem.memory_limit_mb == 128
    assert result.test_names == ["000", "001", "002"]
    assert (result.version_dir / "tests" / "001.in").read_text() == "input 1\n"
    assert (result.version_dir / "tests" / "001.out").read_text() == "output 1\n"
    assert problem.languages == ["c11", "cpp17", "cpp20", "python3"]
    assert (result.version_dir / "meta" / "original" / "task.yaml").is_file()


def test_old_italian_key_names_work(tmp_path: Path) -> None:
    conf = {"nome_breve": "somma", "nome": "Somma", "timeout": 2, "memlimit": 64}
    task = make_task(tmp_path / "task", conf)
    raw = yaml.safe_load((task / "task.yaml").read_text())
    for key in ("name", "title", "time_limit", "memory_limit"):
        del raw[key]
    (task / "task.yaml").write_text(yaml.safe_dump(raw))
    problem = import_problem(task, tmp_path / "store").problem
    assert (problem.slug, problem.title["th"], problem.time_limit_ms, problem.memory_limit_mb) == (
        "somma",
        "Somma",
        2000,
        64,
    )


def test_uppercase_name_is_lowered_with_a_warning(tmp_path: Path) -> None:
    result = run(tmp_path, conf={"name": "Somma"})
    assert result.problem.slug == "somma"
    assert any("lower" in w for w in result.warnings)


def test_memory_over_512_mib_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "512", conf={"memory_limit": 1024})


def test_missing_time_limit_is_refused(tmp_path: Path) -> None:
    task = make_task(tmp_path / "task")
    raw = yaml.safe_load((task / "task.yaml").read_text())
    del raw["time_limit"]
    (task / "task.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ProblemImportError, match="time_limit"):
        import_problem(task, tmp_path / "store")


def test_missing_test_file_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "input3.txt", conf={"n_input": 4})


def test_file_io_defaults_follow_cms(tmp_path: Path) -> None:
    # The CMS loader defaults to input.txt / output.txt; an empty name means stdin / stdout.
    default = run(tmp_path / "a").problem.io
    assert (default.input, default.output) == ("input.txt", "output.txt")
    std = run(tmp_path / "b", conf={"infile": "", "outfile": ""}).problem.io
    assert (std.input, std.output) == ("", "")


def test_attachments_are_copied(tmp_path: Path) -> None:
    result = run(tmp_path, files={"att/somma.cpp": "// template\n", "att/esempio.txt": "1 2\n"})
    assert (result.version_dir / "attachments" / "somma.cpp").read_text() == "// template\n"


# --- scoring: Sum ------------------------------------------------------------------------


def test_without_gen_the_score_type_is_sum_over_n_input(tmp_path: Path) -> None:
    result = run(tmp_path, n_tests=4)
    assert result.problem.scoring == SumScoring(type="sum", total=100)
    assert len(result.test_names) == 4


def test_total_value_sets_the_sum_total(tmp_path: Path) -> None:
    result = run(tmp_path, conf={"total_value": 50})
    assert result.problem.scoring == SumScoring(type="sum", total=50)


def test_without_gen_n_input_is_required(tmp_path: Path) -> None:
    task = make_task(tmp_path / "task")
    raw = yaml.safe_load((task / "task.yaml").read_text())
    del raw["n_input"]
    (task / "task.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ProblemImportError, match="n_input"):
        import_problem(task, tmp_path / "store")


def test_gen_without_subtasks_counts_tests_for_sum(tmp_path: Path) -> None:
    gen = "# a comment\n\n1 2\n3 4 # with a comment\n#COPY: from/elsewhere.txt\n"
    result = run(tmp_path, n_tests=3, gen=gen)
    assert result.problem.scoring == SumScoring(type="sum", total=100)
    assert result.test_names == ["000", "001", "002"]


def test_gen_test_count_must_match_n_input(tmp_path: Path) -> None:
    refused(tmp_path, "n_input", n_tests=3, gen="1\n2\n3\n", conf={"n_input": 2})


# --- scoring: GroupMin from gen/GEN ------------------------------------------------------


GEN_3_SUBTASKS = """\
# ST: 0
1 1
# ST: 40
2 2
2 3
# ST: 60
3 1
3 2
3 3
"""


def test_st_lines_in_gen_make_group_min_subtasks(tmp_path: Path) -> None:
    result = run(tmp_path, n_tests=6, gen=GEN_3_SUBTASKS)
    scoring = result.problem.scoring
    assert isinstance(scoring, GroupMinScoring)
    assert [(s.name, s.points, s.tests) for s in scoring.subtasks] == [
        ("1", 0, ["000"]),
        ("2", 40, ["001", "002"]),
        ("3", 60, ["003", "004", "005"]),
    ]


def test_subtask_points_must_sum_to_100(tmp_path: Path) -> None:
    refused(tmp_path, "100", n_tests=2, gen="# ST: 30\n1\n# ST: 30\n2\n")


def test_tests_before_the_first_st_line_are_refused(tmp_path: Path) -> None:
    refused(tmp_path, "before", n_tests=2, gen="1\n# ST: 100\n2\n")


def test_a_test_and_a_command_on_one_line_are_refused(tmp_path: Path) -> None:
    refused(tmp_path, "same line", n_tests=1, gen="1 2 # ST: 100\n")


def test_st_points_must_be_an_integer(tmp_path: Path) -> None:
    refused(tmp_path, "ST", n_tests=1, gen="# ST: lots\n1\n")


def test_an_empty_subtask_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "no tests", n_tests=1, gen="# ST: 50\n# ST: 50\n1\n")


# --- scoring: explicit score_type in task.yaml -------------------------------------------


def test_group_min_override_with_test_counts(tmp_path: Path) -> None:
    conf = {"score_type": "GroupMin", "score_type_parameters": [[30, 2], [70, 1]]}
    scoring = run(tmp_path, conf=conf, n_tests=3).problem.scoring
    assert isinstance(scoring, GroupMinScoring)
    assert [(s.points, s.tests) for s in scoring.subtasks] == [(30, ["000", "001"]), (70, ["002"])]


def test_group_min_override_with_regexes_uses_re_match(tmp_path: Path) -> None:
    # re.match anchors at the start only, so "00" matches 000, 001 and 002.
    conf = {"score_type": "GroupMin", "score_type_parameters": [[50, "00[01]"], [50, "00"]]}
    scoring = run(tmp_path, conf=conf, n_tests=3).problem.scoring
    assert isinstance(scoring, GroupMinScoring)
    assert [s.tests for s in scoring.subtasks] == [["000", "001"], ["000", "001", "002"]]


def test_group_min_override_regex_matching_nothing_is_refused(tmp_path: Path) -> None:
    conf = {"score_type": "GroupMin", "score_type_parameters": [[100, "9"]]}
    refused(tmp_path, "matches no test", conf=conf)


def test_sum_override_parameter_is_points_per_test(tmp_path: Path) -> None:
    conf = {"score_type": "Sum", "score_type_parameters": 5}
    assert run(tmp_path, conf=conf, n_tests=4).problem.scoring == SumScoring(type="sum", total=20)


def test_score_type_alone_is_ignored_with_a_warning(tmp_path: Path) -> None:
    result = run(tmp_path, conf={"score_type": "GroupMin"})
    assert isinstance(result.problem.scoring, SumScoring)
    assert any("score_type_parameters" in w for w in result.warnings)


def test_other_score_types_are_refused(tmp_path: Path) -> None:
    conf = {"score_type": "GroupMul", "score_type_parameters": [[100, 3]]}
    refused(tmp_path, "GroupMul", conf=conf)


# --- statements --------------------------------------------------------------------------


def test_single_statement_with_default_language_becomes_thai_with_a_warning(
    tmp_path: Path,
) -> None:
    result = run(tmp_path)
    assert (result.version_dir / "statement" / "th.pdf").read_bytes() == PDF
    assert any("primary_language" in w for w in result.warnings)


def test_testo_folder_works_too(tmp_path: Path) -> None:
    result = run(tmp_path, conf={"primary_language": "th"}, statement={"testo/testo.pdf": PDF})
    assert (result.version_dir / "statement" / "th.pdf").is_file()
    assert result.warnings == []


def test_per_language_statements(tmp_path: Path) -> None:
    statement = {"statement/thai.pdf": PDF, "statement/english.pdf": PDF + b"en"}
    result = run(tmp_path, conf={"primary_language": "th"}, statement=statement)
    assert (result.version_dir / "statement" / "en.pdf").read_bytes() == PDF + b"en"
    assert (result.version_dir / "statement" / "th.pdf").read_bytes() == PDF


def test_primary_language_must_have_a_statement(tmp_path: Path) -> None:
    # Like CMS: with per-language files, the primary language (default "it") must be among them.
    refused(tmp_path, "primary", statement={"statement/english.pdf": PDF})


def test_both_statement_folders_are_refused(tmp_path: Path) -> None:
    statement = {"statement/statement.pdf": PDF, "testo/testo.pdf": PDF}
    refused(tmp_path, "testo", statement=statement)


def test_no_statement_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "statement", statement={})


# --- graders, checkers, solutions --------------------------------------------------------


def test_graders_and_headers_from_sol(tmp_path: Path) -> None:
    files = {
        "sol/grader.cpp": "// grader\n",
        "sol/grader.c": "/* grader */\n",
        "sol/somma.h": "int somma(int, int);\n",
    }
    problem = run(tmp_path, files=files).problem
    assert problem.grader is not None
    assert problem.grader.submission_file == "somma.%l"
    assert problem.languages == ["c11", "cpp17", "cpp20"]
    assert problem.grader.files == {
        "c11": ["graders/grader.c", "graders/somma.h"],
        "cpp17": ["graders/grader.cpp", "graders/somma.h"],
        "cpp20": ["graders/grader.cpp", "graders/somma.h"],
    }


def test_graders_only_for_unsupported_languages_are_refused(tmp_path: Path) -> None:
    refused(tmp_path, "grader", files={"sol/grader.java": "class X {}\n"})


def test_checker_source_becomes_a_cms_checker(tmp_path: Path) -> None:
    for folder, name in (("check", "checker"), ("cor", "correttore")):
        result = run(tmp_path / folder, files={f"{folder}/{name}.cpp": "int main() {}\n"})
        assert result.problem.checker.type == "cms"
        assert result.problem.checker.file == "checker/checker.cpp"
        assert (result.version_dir / "checker" / "checker.cpp").read_text() == "int main() {}\n"


def test_checker_binary_without_source_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "source", files={"check/checker": b"\x7fELF binary"})


def test_no_checker_means_white_diff(tmp_path: Path) -> None:
    assert run(tmp_path).problem.checker.type == "diff"


def test_solutions_from_sol(tmp_path: Path) -> None:
    files = {
        "sol/soluzione.cpp": "int main() {}\n",
        "sol/slow.py": "print()\n",
        "sol/grader.cpp": "// grader\n",
        "sol/somma.h": "\n",
        "sol/notes.txt": "x\n",
    }
    result = run(tmp_path, files=files)
    solutions = {(s.file, s.lang, s.expected_score) for s in result.problem.solutions}
    # A grader task limits languages to the ones with a grader, so slow.py is left out.
    assert solutions == {("solutions/soluzione.cpp", "cpp17", 100)}
    assert any("slow.py" in w for w in result.warnings)


def test_solution_expected_score_is_the_full_score(tmp_path: Path) -> None:
    files = {"sol/solution.py": "print()\n", "sol/try.c": "int main() {}\n"}
    result = run(tmp_path, conf={"total_value": 50}, files=files)
    solutions = {(s.file, s.lang, s.expected_score) for s in result.problem.solutions}
    assert solutions == {("solutions/solution.py", "python3", 50), ("solutions/try.c", "c11", None)}


# --- public tests and samples ------------------------------------------------------------


def test_public_testcases_are_reported_but_not_published(tmp_path: Path) -> None:
    result = run(tmp_path, conf={"public_testcases": "0, 2"})
    assert result.problem.samples == []
    assert any("000, 002" in w for w in result.warnings)


def test_samples_key_picks_the_samples(tmp_path: Path) -> None:
    assert run(tmp_path, conf={"samples": "0,1"}).problem.samples == ["000", "001"]
    assert run(tmp_path / "b", conf={"samples": [2]}).problem.samples == ["002"]


# --- unsupported task types --------------------------------------------------------------


def test_output_only_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "OutputOnly", conf={"output_only": True})


@pytest.mark.parametrize("manager", ["check/manager", "cor/manager"])
def test_communication_is_refused(tmp_path: Path, manager: str) -> None:
    refused(tmp_path, "Communication", files={manager: b"\x7fELF"})


@pytest.mark.parametrize(
    "conf",
    [{"n_input": "three"}, {"total_value": "lots"}, {"time_limit": "fast"}, {"n_input": 2.5}],
)
def test_non_numeric_values_give_a_clear_error(tmp_path: Path, conf: dict[str, object]) -> None:
    refused(tmp_path, "must be", conf=conf)


@pytest.mark.parametrize("value", [["th"], {"a": 1}, 5])
def test_primary_language_must_be_text(tmp_path: Path, value: object) -> None:
    refused(tmp_path, "primary_language", conf={"primary_language": value})
