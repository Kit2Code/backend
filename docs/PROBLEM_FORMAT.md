# Problem format

How to package a problem for the grader. Two formats can be imported:

- **Native format** (this page): a folder with a `problem.yaml`.
- **CMS "Italian" format**: a CMS task folder with a `task.yaml`. It is converted to the native
  format on import; see [CMS Italian format](#cms-italian-format) below.

Upload either one as a **zip** on the admin site's Import page, or import it from the command line:

```sh
uv run grader-import path/to/problem-folder-or.zip --store .devdata/problems
```

You can also make a native problem without a zip: the admin site's **New problem** page writes this
format from a form (statement, test files, scoring, checker, reference solutions) and imports it
the same way. **Edit** on the Problems page starts from the current version and saves a new one.

The command prints the stored version folder, for example `.devdata/problems/aplusb/v001-d8a8e4e9`.
Warnings go to stderr. If the problem is refused, it prints one `grader-import: error: …` line and
stores nothing. Working examples: [`fixtures/problems/`](../fixtures/problems/).

## Folder layout

```
problem.yaml          the description (below)
statement/            th.pdf, en.pdf, th.md and/or en.md (at least one)
attachments/          files contestants may download (templates, examples)
tests/                <name>.in and <name>.out for every test
checker/checker.cpp   only for a cms or testlib checker
graders/              grader stubs and headers, only for problems with a grader
solutions/            reference solutions, used to validate the problem
```

Anything else is left out with a warning. **Never shown to contestants:** `tests/` (except the
samples), `checker/`, `solutions/`, and any file in `graders/` that isn't listed as downloadable.

Test names may use letters, digits, `_`, `-` and `.` (not as the first character), up to 64
characters. Every `tests/<name>.in` needs a `tests/<name>.out`.

## `problem.yaml`

```yaml
format_version: 1
slug: aplusb                      # web address: a-z, 0-9, _ and -; at most 64
title: {th: "เอบวกบี", en: "A plus B"}   # at least one of th / en
source: "Contest name, year, day"  # optional
tags: [math]                      # optional
time_limit_ms: 1000               # 1 to 60000
memory_limit_mb: 256              # 16 to 512
languages: [c11, cpp17, cpp20, python3]
lang_time_factor: {python3: 1.0}  # optional: multiplies the time limit per language (above 0, at most 10)
io: {input: "", output: ""}       # optional: "" = stdin / stdout, or a file name like input.txt
scoring:
  type: group_min
  subtasks:
    - {name: "1", points: 30, tests: ["1-*"]}
    - {name: "2", points: 70, tests: ["1-*", "2-*"]}
samples: ["1-01"]                 # tests shown on the problem page; judged like the others
checker: {type: diff}             # diff | cms | testlib (cms and testlib need file:)
solutions:
  - {file: solutions/full.cpp, lang: cpp17, expected_score: 100}
  - {file: solutions/partial.cpp, lang: cpp17, expected_score: 30}
  - {file: solutions/idea.py, lang: python3}   # no expected_score: run and reported only
visibility_default: hidden
license: "who may use this problem"
```

Unknown keys are errors, so a typo can't silently turn into a default. File and folder names in
paths (checker, graders, solutions) may not start with `-`, so they can't be mistaken for compiler
options. The `io` file names may not be names the judge uses itself in the sandbox: `_stdin`,
`_stdout`, `_stderr`, or the program's executable (`main` and `main.zip`, or with a grader the
`submission_file` name without `.%l`, plus `.zip`), and a file input and file output must have
different names. The full schema is
generated into [`common/grader_common/schema/problem.schema.json`](../common/grader_common/schema/problem.schema.json).

> **Quote test names that are only digits.** In YAML, `samples: [01]` is the *number* 1, not the
> test `01`. Write `samples: ["01"]`.

### Scoring

The same rules as CMS v1.5.1. Each test gets an **outcome** from 0 (wrong) to 1 (right); values in
between are partial credit from a checker.

- **`sum`** (`{type: sum, total: 100}`): every test is worth `total / number of tests`, times its
  outcome.
- **`group_min`**: each subtask scores its `points` times the **lowest** outcome among its tests.
  `tests` are name patterns (`*` any text, `?` one character, `[12]` one of the characters). A test
  may be in several subtasks. A pattern that matches no test is an error.

Tests are ordered by name as text, as in CMS (`10` comes before `2`), so pad numbers: `01`, `02`, …

### Checkers

- **`diff`**: CMS "white-diff". The output must match line by line. Within a line, the amount of
  spaces or tabs and any leading or trailing whitespace don't matter, and blank lines at the end are
  ignored, but line breaks do matter (`1 2` on one line is not the same as `1` and `2` on two lines).
- **`cms`**: `checker/checker.cpp` is run as `checker <input> <correct output> <contestant output>`.
  It prints the outcome (0.0 to 1.0) on the first line of stdout and a message on stderr
  (`translate:success`, `translate:wrong` or `translate:partial` are shown in the user's language).
- **`testlib`**: `checker/checker.cpp` uses [testlib](https://github.com/MikeMirzayanov/testlib)
  (`#include "testlib.h"`, which the judge supplies) and is run as
  `checker <input> <contestant output> <answer>`.

The judge compiles checkers itself, inside the sandbox.

### Graders

For problems where contestants write a function instead of a whole program:

```yaml
languages: [c11, cpp17, cpp20]
grader:
  submission_file: "adder.%l"     # %l becomes the language's extension
  files:
    c11: [graders/grader.c, graders/adder.h]
    cpp17: [graders/grader.cpp, graders/adder.h]
    cpp20: [graders/grader.cpp, graders/adder.h]
  downloadable: [graders/adder.h] # shown to contestants
```

### Solutions and validation

A newly imported problem stays **hidden** until every solution has been judged and each one with an
`expected_score` got exactly that score. Solutions without `expected_score` are judged and reported
only.

## Versions

Every import creates an **immutable version** folder `<slug>/v<NNN>-<hash>/`. The hash covers every
file's name and content, so importing the same problem again changes nothing, and importing a
changed problem creates the next version. Submissions remember the version they were judged on.
Each version has `meta/import-report.json` (format, warnings, content hash).

## Limits on uploads

At most 10 000 files, 1 GB unpacked, and 256 MB for any single file. Symlinks, device files,
absolute paths, `..`, backslashes and encrypted zip entries are refused. A single folder wrapping
everything in a zip is fine. macOS `__MACOSX/` and `.DS_Store` files are ignored.

## CMS Italian format

A CMS task folder (`task.yaml`, `statement/` or `testo/`, `input/inputN.txt`, `output/outputN.txt`,
optional `gen/GEN`, `check/`, `sol/`, `att/`) is converted following CMS v1.5.1's loader
(`cmscontrib/loaders/italy_yaml.py`):

| CMS | Here |
|---|---|
| `name` (or `nome_breve`), `title` (or `nome`) | `slug` (lowercased), `title.th` |
| `time_limit` (or `timeout`) in seconds | `time_limit_ms` |
| `memory_limit` (or `memlimit`) in MiB | `memory_limit_mb` (at most 512) |
| `infile` / `outfile` (default `input.txt` / `output.txt`) | `io` |
| `gen/GEN` with `# ST: <points>` lines | `group_min`, consecutive tests per subtask (points must add up to 100) |
| no `# ST:` lines | `sum` with `total_value` (default 100) over `n_input` tests |
| `score_type` + `score_type_parameters` (`Sum` or `GroupMin`) | the same scoring |
| test `i` | test `000`, `001`, … |
| `statement/statement.pdf` or `statement/<language>.pdf` | `statement/th.pdf`, `statement/en.pdf` |
| `check/checker.cpp` or `cor/correttore.cpp` | `cms` checker |
| `sol/grader.c`, `sol/grader.cpp`, `sol/grader.py`, `sol/*.h` | `grader` (languages limited to those with a grader) |
| other `.c`, `.cpp`, `.py` files in `sol/` | `solutions` (`soluzione.*` / `solution.*` must get full points) |
| `att/*` | `attachments/` |

Differences from CMS, on purpose:

- `public_testcases` are **not** published as samples (CMS only showed their results). Add
  `samples: "0, 1"` (or a list of test numbers) to `task.yaml` to choose samples.
- The checker's **source** is required; a compiled `check/checker` alone is refused.
- Only Thai and English statements are kept. If there is neither, the primary-language statement is
  stored as Thai (with a warning).
- OutputOnly and Communication tasks, and score types other than Sum and GroupMin, are refused.
