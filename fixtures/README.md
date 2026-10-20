# Test fixtures

Small **made-up** problems used by the tests and the dev stack. Never put real contest problems,
tests or solutions here; real problems are uploaded on the server only.

## `problems/`: native format

| Problem | What it exercises |
|---|---|
| `aplusb` | Sum scoring; C++ and Python solutions; `overflow.cpp` (no expected score) fails the last test |
| `groupmin` | GroupMin with 3 kinds of tests; `partial.cpp` scores exactly 30 (wrong on the big values) |
| `cms-checker` | CMS-style checker giving partial credit; `trivial.py` scores 50 |
| `testlib-checker` | testlib checker (`testlib.h` is supplied by the judge) accepting any permutation |
| `grader-stub` | Batch with a grader stub in C and C++; contestants implement `add()` |
| `python-only` | a problem that allows only Python |
| `big-output` | `flood.cpp` prints forever, to test the output limit (expected score 0) |

## `cms-italian/`: CMS Italian format

The same tasks as far as that format can express them (CMS has no testlib checkers and no
per-task language list, so `testlib-checker` and `python-only` exist only above). The expected
result of importing each one is in `common/tests/importer/golden/`.

| Task | What it exercises |
|---|---|
| `aplusb` | Sum from `n_input`; stdin/stdout; `public_testcases` and `samples` |
| `groupmin` | GroupMin from `gen/GEN` `# ST:` lines; file I/O (`input.txt` / `output.txt`); Thai and English statements |
| `cms-checker` | `check/checker.cpp`; `testo/` folder; no `primary_language` (CMS default `it`) |
| `grader-stub` | `sol/grader.c`, `sol/grader.cpp` and `sol/adder.h`; an attachment |
| `big-output` | explicit `score_type: Sum` with points per test |
| `communication` | a Communication task, which must be refused |
