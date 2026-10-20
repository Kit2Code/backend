"""Convert a task in CMS's "Italian" format into our native layout.

The rules re-implement `cmscontrib/loaders/italy_yaml.py` from CMS v1.5.1 (commit 7c9fec8), mostly
`YamlLoader.get_task`; comments cite its line numbers. We don't import CMS itself. Where CMS
would crash (an `assert`, a missing key) we stop with a clear message instead. Where our format has
no equivalent, the choice and the reason are in docs/DECISIONS.md:

- CMS "public" tests are not turned into samples; an optional `samples:` key chooses them.
- The checker must be source code (`check/checker.cpp` or `cor/correttore.cpp`).
- Reference solutions come from `sol/`; CMS itself ignores them.
- A statement in a language other than Thai or English is stored as Thai, with a warning.
"""

import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from grader_common.importer.errors import ProblemImportError
from grader_common.importer.native import describe_error
from grader_common.problem_format import Language, Problem

_MAX_YAML_BYTES = 1 << 20
ALL_LANGUAGES: list[Language] = ["c11", "cpp17", "cpp20", "python3"]

# cmscontrib/loaders/base_loader.py, LANGUAGE_MAP (CMS v1.5.1): "<language>.pdf" -> language code.
LANGUAGE_MAP = {
    "afrikaans": "af", "arabic": "ar", "armenian": "hy", "azerbaijani": "az", "belarusian": "be",
    "bengali": "bn", "bosnian": "bs", "bulgarian": "bg", "catalan": "ca", "chinese": "zh",
    "croatian": "hr", "czech": "cs", "danish": "da", "dutch": "nl", "english": "en",
    "estonian": "et", "filipino": "fil", "finnish": "fi", "french": "fr", "georgian": "ka",
    "german": "de", "greek": "el", "hebrew": "he", "hindi": "hi", "hungarian": "hu",
    "icelandic": "is", "indonesian": "id", "irish": "ga", "italian": "it", "japanese": "ja",
    "kazakh": "kk", "korean": "ko", "kyrgyz": "ky", "latvian": "lv", "lithuanian": "lt",
    "macedonian": "mk", "malay": "ms", "mongolian": "mn", "norwegian": "no", "persian": "fa",
    "polish": "pl", "portuguese": "pt", "romanian": "ro", "russian": "ru", "serbian": "sr",
    "sinhala": "si", "slovak": "sk", "slovene": "sl", "spanish": "es", "swedish": "sv",
    "tajik": "tg", "tamil": "ta", "thai": "th", "turkish": "tr", "turkmen": "tk",
    "ukrainian": "uk", "urdu": "ur", "uzbek": "uz", "vietnamese": "vi", "other": "other",
}  # fmt: skip

# The main source extension of every CMS v1.5.1 language (cms/grading/languages/*.py). A file
# sol/grader<ext> for any of them makes CMS treat the task as "Batch with grader".
CMS_GRADER_EXTENSIONS = (".c", ".cpp", ".cs", ".hs", ".java", ".pas", ".php", ".py", ".rs")
CMS_HEADER_EXTENSIONS = (".h", "lib.pas")
# Which of our languages each grader file serves.
GRADER_LANGUAGES: dict[str, list[Language]] = {
    ".c": ["c11"],
    ".cpp": ["cpp17", "cpp20"],
    ".py": ["python3"],
}
SOLUTION_LANGUAGES: dict[str, Language] = {
    ".c": "c11",
    ".cpp": "cpp17",
    ".cc": "cpp17",
    ".cxx": "cpp17",
    ".py": "python3",
}
FULL_SCORE_SOLUTION_NAMES = ("soluzione", "solution")


def convert(task: Path, out: Path) -> list[str]:
    """Write the native version folder `out` for the Italian-format task in `task`.

    Returns warnings for the import report. Raises ProblemImportError if the task can't be imported.
    """
    conf = _load_task_yaml(task / "task.yaml")
    warnings: list[str] = []
    out.mkdir()

    # Lines 378-379: the short name and the title.
    name = _required(conf, ["name", "nome_breve"])
    title = _required(conf, ["title", "nome"])
    if not isinstance(name, str) or not isinstance(title, str):
        raise ProblemImportError("task.yaml: name and title must be text")
    slug = name.lower()
    if slug != name:
        warnings.append(f"name {name!r}: lowercased to {slug!r} for the web address")

    # Lines 694-755: OutputOnly and Communication tasks can't be judged here (yet).
    if conf.get("output_only", False):
        raise ProblemImportError("OutputOnly tasks are not supported")
    for manager in ("check/manager", "cor/manager"):
        if (task / manager).exists():
            raise ProblemImportError(f"{manager}: Communication tasks are not supported")

    _copy_statements(task, out, conf, warnings)
    _copy_attachments(task, out, warnings)

    # Lines 540-543: time limit in seconds, memory limit in MiB.
    time_limit_ms = round(_number(_required(conf, ["time_limit", "timeout"]), "time_limit") * 1000)
    memory_mib = _number(_required(conf, ["memory_limit", "memlimit"]), "memory_limit")
    if memory_mib > 512:
        raise ProblemImportError(f"memory_limit {memory_mib:g} MiB: the maximum here is 512 MiB")
    if not float(memory_mib).is_integer():
        raise ProblemImportError(f"memory_limit {memory_mib:g}: must be a whole number of MiB")

    # Lines 547-548: file names the program reads and writes ("" = stdin / stdout).
    io = {"input": conf.get("infile", "input.txt"), "output": conf.get("outfile", "output.txt")}

    languages, grader = _graders(task, out, name, warnings)
    checker = _checker(task, out)
    scoring, n_input = _scoring(task, conf, warnings)
    test_names = _copy_tests(task, out, n_input, warnings)
    scoring = _resolve_counts(scoring, test_names)
    if scoring["type"] == "sum":
        full_score = float(scoring["total"])
    else:
        full_score = float(sum(subtask["points"] for subtask in scoring["subtasks"]))

    public = _test_selection(conf, ["public_testcases", "risultati"], test_names)
    if public:
        warnings.append(
            f"public_testcases {', '.join(public)}: CMS showed these results to contestants; "
            "they are not published as samples (add `samples:` to task.yaml to publish some)"
        )
    samples = _test_selection(conf, ["samples"], test_names)

    data: dict[str, Any] = {
        "format_version": 1,
        "slug": slug,
        "title": {"th": title},
        "time_limit_ms": time_limit_ms,
        "memory_limit_mb": int(memory_mib),
        "languages": languages,
        "io": io,
        "scoring": scoring,
        "samples": samples,
        "checker": checker,
        "solutions": _copy_solutions(task, out, languages, full_score, warnings),
    }
    if grader is not None:
        data["grader"] = grader
    try:
        problem = Problem.model_validate(data)
    except ValidationError as error:
        raise ProblemImportError("converted task: " + describe_error(error)) from None
    dumped = problem.model_dump(mode="json", exclude_none=True)
    (out / "problem.yaml").write_text(
        yaml.safe_dump(dumped, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    original = out / "meta" / "original"
    original.mkdir(parents=True)
    shutil.copyfile(task / "task.yaml", original / "task.yaml")
    if (task / "gen" / "GEN").is_file():
        shutil.copyfile(task / "gen" / "GEN", original / "GEN")
    return warnings


# --- task.yaml ---------------------------------------------------------------------------


def _load_task_yaml(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_YAML_BYTES:
        raise ProblemImportError("task.yaml is larger than 1 MB")
    try:
        conf = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise ProblemImportError(f"task.yaml is not valid YAML: {error}") from None
    if not isinstance(conf, dict):
        raise ProblemImportError("task.yaml must be a mapping (key: value pairs)")
    return conf


def _first(conf: dict[str, Any], keys: list[str]) -> tuple[bool, Any]:
    """CMS's `load()` (lines 75-124): the value of the first of `keys` that is present."""
    for key in keys:
        if key in conf:
            return True, conf[key]
    return False, None


def _required(conf: dict[str, Any], keys: list[str]) -> Any:
    found, value = _first(conf, keys)
    if not found or value is None:
        raise ProblemImportError(
            f"task.yaml: {' (or '.join(keys)}{')' * (len(keys) - 1)} is missing"
        )
    return value


def _number(value: Any, key: str) -> float:
    """CMS converts with float(), so "1.5" works as well as 1.5."""
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
    except (TypeError, ValueError):
        raise ProblemImportError(f"task.yaml: {key} must be a number") from None
    if not math.isfinite(number):
        raise ProblemImportError(f"task.yaml: {key} must be a number")
    return number


def _whole(value: Any, key: str) -> int:
    """CMS converts with int(); unlike int(), we refuse 2.5 instead of silently making it 2."""
    number = _number(value, key)
    if not number.is_integer():
        raise ProblemImportError(f"task.yaml: {key} must be a whole number")
    return int(number)


# --- statements and attachments ----------------------------------------------------------


def _copy_statements(task: Path, out: Path, conf: dict[str, Any], warnings: list[str]) -> None:
    """Lines 394-458: statement/ or testo/, one PDF or one per language."""
    primary = conf.get("primary_language") or "it"
    if not isinstance(primary, str):
        raise ProblemImportError("task.yaml: primary_language must be a language code like 'th'")
    folders = [name for name in ("statement", "testo") if (task / name).exists()]
    if len(folders) > 1:
        raise ProblemImportError("both statement/ and testo/ are present; keep only one")
    if not folders:
        raise ProblemImportError("no statement/ (or testo/) folder")
    folder = task / folders[0]

    single = folder / f"{folders[0]}.pdf"
    per_language = {
        code: folder / f"{lang}.pdf"
        for lang, code in LANGUAGE_MAP.items()
        if (folder / f"{lang}.pdf").is_file()
    }
    if per_language:
        if single.is_file():
            warnings.append(f"{folders[0]}/{single.name} ignored: per-language statements found")
        chosen = per_language
    elif single.is_file():
        chosen = {primary: single}
    else:
        raise ProblemImportError(f"no statement PDF in {folders[0]}/")
    if primary not in chosen:
        raise ProblemImportError(f"no statement for the primary language {primary!r}")

    ours = {code: path for code, path in chosen.items() if code in ("th", "en")}
    for code in sorted(set(chosen) - set(ours)):
        if ours:
            warnings.append(f"statement in language {code!r} left out (only th and en are kept)")
    if not ours:
        ours = {"th": chosen[primary]}
        warnings.append(f"primary_language {primary!r}: statement stored as the Thai (th) one")
    (out / "statement").mkdir()
    for code, path in ours.items():
        shutil.copyfile(path, out / "statement" / f"{code}.pdf")


def _copy_attachments(task: Path, out: Path, warnings: list[str]) -> list[str]:
    """Lines 521-528: every file in att/ is given to contestants."""
    att = task / "att"
    if not att.is_dir():
        return []
    names = []
    for path in sorted(att.iterdir()):
        if not path.is_file():
            warnings.append(f"att/{path.name}: folders in att/ are not supported, left out")
            continue
        (out / "attachments").mkdir(exist_ok=True)
        shutil.copyfile(path, out / "attachments" / path.name)
        names.append(path.name)
    return names


# --- graders and checker -----------------------------------------------------------------


def _graders(
    task: Path, out: Path, name: str, warnings: list[str]
) -> tuple[list[Language], dict[str, Any] | None]:
    """Lines 550-585: sol/grader.<ext> makes the task "Batch with grader"; headers come along."""
    sol = task / "sol"
    found = [ext for ext in CMS_GRADER_EXTENSIONS if (sol / f"grader{ext}").is_file()]
    if not found:
        return list(ALL_LANGUAGES), None
    headers = sorted(
        p.name for p in sol.iterdir() if p.is_file() and p.name.endswith(CMS_HEADER_EXTENSIONS)
    )
    c_headers = [h for h in headers if h.endswith(".h")]
    (out / "graders").mkdir()
    for header in c_headers:
        shutil.copyfile(sol / header, out / "graders" / header)
    files: dict[Language, list[str]] = {}
    for ext in found:
        if ext not in GRADER_LANGUAGES:
            warnings.append(f"sol/grader{ext}: language not supported here, left out")
            continue
        shutil.copyfile(sol / f"grader{ext}", out / "graders" / f"grader{ext}")
        extra = [f"graders/{h}" for h in c_headers] if ext in (".c", ".cpp") else []
        for lang in GRADER_LANGUAGES[ext]:
            files[lang] = [f"graders/grader{ext}", *extra]
    if not files:
        raise ProblemImportError("sol/ has graders only for languages that aren't supported here")
    languages = [lang for lang in ALL_LANGUAGES if lang in files]
    return languages, {"submission_file": f"{name}.%l", "files": files}


def _checker(task: Path, out: Path) -> dict[str, Any]:
    """Lines 587-602: check/checker or cor/correttore turns on CMS's "comparator" checker.

    CMS stores the compiled checker; we compile it ourselves inside the sandbox, so we need its
    source file next to it.
    """
    for folder, name in (("check", "checker"), ("cor", "correttore")):
        source = task / folder / f"{name}.cpp"
        if source.is_file():
            (out / "checker").mkdir()
            shutil.copyfile(source, out / "checker" / "checker.cpp")
            return {"type": "cms", "file": "checker/checker.cpp"}
        if (task / folder / name).exists():
            raise ProblemImportError(
                f"{folder}/{name}: the checker's source {folder}/{name}.cpp is needed "
                "(a compiled checker can't be imported)"
            )
    return {"type": "diff"}


# --- scoring and tests -------------------------------------------------------------------


def _scoring(task: Path, conf: dict[str, Any], warnings: list[str]) -> tuple[dict[str, Any], int]:
    """Lines 604-692: Sum or GroupMin from gen/GEN, then an optional explicit override.

    Returns the scoring (GroupMin targets may still be test counts, see `_resolve_counts`) and the
    number of tests.
    """
    gen = task / "gen" / "GEN"
    if gen.is_file():
        subtasks, n_input = _parse_gen(gen.read_text(encoding="utf-8"))
        if "n_input" in conf and _whole(conf["n_input"], "n_input") != n_input:
            raise ProblemImportError(
                f"n_input is {conf['n_input']} but gen/GEN describes {n_input} tests"
            )
    else:
        if "n_input" not in conf:
            raise ProblemImportError("task.yaml: n_input is missing (and there is no gen/GEN)")
        subtasks, n_input = None, _whole(conf["n_input"], "n_input")

    total_value = _number(conf.get("total_value", 100.0), "total_value")
    if subtasks is None:
        scoring: dict[str, Any] = {"type": "sum", "total": total_value}
    else:
        scoring = {"type": "group_min", "subtasks": subtasks}

    has_type, has_parameters = "score_type" in conf, "score_type_parameters" in conf
    if has_type and has_parameters:
        scoring = _explicit_scoring(conf["score_type"], conf["score_type_parameters"], n_input)
    elif has_type or has_parameters:
        warnings.append(
            "score_type and score_type_parameters must both be given to override scoring; ignored"
        )
    return scoring, n_input


def _parse_gen(text: str) -> tuple[list[dict[str, Any]] | None, int]:
    """Lines 606-668: count tests in gen/GEN; `# ST: <points>` lines start subtasks."""
    subtasks: list[list[int]] = []
    testcases = 0
    points: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        splitted = line.split("#", 1)
        if len(splitted) == 1:
            if splitted[0] != "":
                testcases += 1
            continue
        testcase, comment = splitted[0].strip(), splitted[1].strip()
        flags = [len(testcase) > 0, comment.startswith("COPY:"), comment.startswith("ST:")]
        if sum(flags) > 1:
            raise ProblemImportError(
                f"gen/GEN: a test and a command on the same line are not allowed: {line!r}"
            )
        if flags[0] or flags[1]:
            testcases += 1
        if flags[2]:
            if points is None:
                if testcases != 0:
                    raise ProblemImportError("gen/GEN: tests before the first '# ST:' line")
            else:
                subtasks.append([points, testcases])
            testcases = 0
            try:
                points = int(comment[3:].strip())
            except ValueError:
                raise ProblemImportError(f"gen/GEN: bad '# ST:' points in {line!r}") from None
    if points is None:
        return None, testcases
    subtasks.append([points, testcases])
    if sum(st[0] for st in subtasks) != 100:
        raise ProblemImportError("gen/GEN: the '# ST:' points must add up to 100")
    n_input = sum(st[1] for st in subtasks)
    return [_subtask(i, st[0], st[1]) for i, st in enumerate(subtasks)], n_input


def _explicit_scoring(score_type: Any, parameters: Any, n_input: int) -> dict[str, Any]:
    """Lines 683-688 and cms/grading/scoretypes/{Sum,GroupMin}.py: the override in task.yaml."""
    if score_type == "Sum":
        # Sum's parameter is the value of one test.
        return {"type": "sum", "total": _number(parameters, "score_type_parameters") * n_input}
    if score_type == "GroupMin":
        if not isinstance(parameters, list) or not parameters:
            raise ProblemImportError("score_type_parameters for GroupMin must be a list")
        subtasks = []
        for index, item in enumerate(parameters):
            if not isinstance(item, list) or len(item) < 2:
                raise ProblemImportError("each GroupMin parameter must be [points, tests]")
            subtasks.append(_subtask(index, _number(item[0], "points"), item[1]))
        targets = [s["target"] for s in subtasks]
        if not (all(_is_int(t) for t in targets) or all(isinstance(t, str) for t in targets)):
            # cms/grading/scoretypes/abc.py, retrieve_target_testcases
            raise ProblemImportError("GroupMin tests must be all counts or all regular expressions")
        return {"type": "group_min", "subtasks": subtasks}
    raise ProblemImportError(f"score type {score_type!r} is not supported (only Sum and GroupMin)")


def _subtask(index: int, points: float, target: Any) -> dict[str, Any]:
    """A subtask whose tests are still a count or a regex (see `_resolve_counts`)."""
    return {"name": str(index + 1), "points": points, "target": target}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _resolve_counts(scoring: dict[str, Any], test_names: list[str]) -> dict[str, Any]:
    """cms/grading/scoretypes/abc.py, retrieve_target_testcases: counts or regexes -> test names.

    Counts take tests in lexicographic order of their names, one subtask after the other. A regex
    selects the names it `re.match`es (anchored at the start only).
    """
    if scoring["type"] != "group_min":
        return scoring
    ordered = sorted(test_names)
    current = 0
    subtasks = []
    for subtask in scoring["subtasks"]:
        target = subtask["target"]
        if _is_int(target):
            tests = ordered[current : current + target]
            current += target
        else:
            try:
                pattern = re.compile(target)
            except re.error as error:
                raise ProblemImportError(f"subtask {subtask['name']}: bad regex: {error}") from None
            tests = [name for name in ordered if pattern.match(name)]
            if not tests:
                raise ProblemImportError(
                    f"subtask {subtask['name']}: regex {target!r} matches no test"
                )
        if not tests:
            raise ProblemImportError(f"subtask {subtask['name']} has no tests")
        subtasks.append({"name": subtask["name"], "points": subtask["points"], "tests": tests})
    return {"type": "group_min", "subtasks": subtasks}


def _copy_tests(task: Path, out: Path, n_input: int, warnings: list[str]) -> list[str]:
    """Lines 764-773: input/input<i>.txt and output/output<i>.txt become test "%03d" % i."""
    (out / "tests").mkdir()
    names = []
    for i in range(n_input):
        codename = f"{i:03d}"  # CMS: "%03d" % i
        for kind, suffix in (("input", "in"), ("output", "out")):
            source = task / kind / f"{kind}{i}.txt"
            if not source.is_file():
                raise ProblemImportError(f"{kind}/{kind}{i}.txt is missing")
            shutil.copyfile(source, out / "tests" / f"{codename}.{suffix}")
        names.append(codename)
    extra = len(list((task / "input").glob("input*.txt"))) - n_input if n_input else 0
    if extra > 0:
        warnings.append(f"input/: {extra} more input file(s) than tests, left out")
    return names


def _test_selection(conf: dict[str, Any], keys: list[str], test_names: list[str]) -> list[str]:
    """Lines 777-784: "all", or comma-separated test numbers like "0, 2"."""
    _, value = _first(conf, keys)
    if value is None or value == "":
        return []
    if value == "all":
        return list(test_names)
    if isinstance(value, str):
        items: list[Any] = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    selected = []
    for item in items:
        try:
            index = int(item)
        except (TypeError, ValueError):
            raise ProblemImportError(
                f"task.yaml: {keys[0]}: {item!r} is not a test number"
            ) from None
        if not 0 <= index < len(test_names):
            raise ProblemImportError(f"task.yaml: {keys[0]}: there is no test {index}")
        selected.append(test_names[index])
    return selected


# --- reference solutions -----------------------------------------------------------------


def _copy_solutions(
    task: Path, out: Path, languages: list[Language], full_score: float, warnings: list[str]
) -> list[dict[str, Any]]:
    """Every C/C++/Python file in sol/ except graders, stubs and headers.

    `soluzione.*` and `solution.*` are expected to get the full score; the others are run and
    reported but not required to reach any score.
    """
    sol = task / "sol"
    if not sol.is_dir():
        return []
    solutions = []
    for path in sorted(sol.iterdir()):
        stem, ext = os.path.splitext(path.name)
        lang = SOLUTION_LANGUAGES.get(ext)
        if not path.is_file() or lang is None or stem in ("grader", "stub"):
            continue
        if path.name.endswith(CMS_HEADER_EXTENSIONS):
            continue
        if lang not in languages:
            warnings.append(f"sol/{path.name}: {lang} is not allowed for this task, left out")
            continue
        (out / "solutions").mkdir(exist_ok=True)
        shutil.copyfile(path, out / "solutions" / path.name)
        expected = full_score if stem.lower() in FULL_SCORE_SOLUTION_NAMES else None
        solutions.append(
            {"file": f"solutions/{path.name}", "lang": lang, "expected_score": expected}
        )
    return solutions
