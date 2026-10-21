"""White-diff must agree with CMS v1.5.1 on every golden case.

golden/whitediff.json was produced by running CMS's own `_white_diff` (copied verbatim from
cms/grading/steps/whitediff.py) on the corpus listed in it, not by our code.
"""

import io
import json
import random
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from grader_worker import whitediff
from grader_worker.whitediff import white_diff

GOLDEN = json.loads((Path(__file__).parent / "golden" / "whitediff.json").read_text())
CASES: list[dict[str, Any]] = GOLDEN["cases"]


@pytest.mark.parametrize("case", CASES, ids=[str(i) for i in range(len(CASES))])
def test_matches_cms(case: dict[str, Any]) -> None:
    output = case["output"].encode("latin-1")
    correct = case["correct"].encode("latin-1")
    assert white_diff(io.BytesIO(output), io.BytesIO(correct)) is case["equal"]


def test_golden_corpus_covers_both_answers() -> None:
    assert len(CASES) >= 40
    assert any(c["equal"] for c in CASES) and not all(c["equal"] for c in CASES)


def test_long_outputs_are_streamed() -> None:
    line = b"1 2 3 4 5 6 7 8 9 10\n"
    big = line * 200_000  # 4 MB
    assert white_diff(io.BytesIO(big), io.BytesIO(big.replace(b" ", b"  ")))
    assert not white_diff(io.BytesIO(big), io.BytesIO(big[:-2] + b"1\n"))


def test_one_huge_line_is_compared_in_little_memory() -> None:
    """A 32 MB output on a single line must not be split into millions of tokens in memory.

    The worker compares outputs outside any sandbox, so its memory use must not grow with the
    number of tokens on a line (a contestant controls that).
    """
    output = io.BytesIO(b"1 " * (16 << 20))  # 32 MB, one line
    correct = io.BytesIO(b" 1" * (16 << 20) + b"  \n\n")  # same tokens, other spacing
    different = io.BytesIO(b"1 " * ((16 << 20) - 1) + b"2 ")
    tracemalloc.start()
    try:
        assert white_diff(output, correct)
        output.seek(0)
        assert not white_diff(output, different)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 50 << 20, f"peak memory {peak >> 20} MB"


def test_tokens_split_across_reads_are_joined() -> None:
    """Spacing and tokens that straddle the reader's chunk boundaries compare like any other."""
    for size in (1, 2, 3, 65535, 65536, 65537):
        token = b"x" * size
        line = token + b" \t " + token + b"\r\n"
        assert white_diff(io.BytesIO(line * 3), io.BytesIO((token + b" " + token + b"\n") * 3))
        assert not white_diff(io.BytesIO(line * 3), io.BytesIO((token + token + b"\n") * 3))
        assert not white_diff(io.BytesIO(line * 3), io.BytesIO((token + b"\n" + token + b"\n") * 3))


def _cms_white_diff(output: bytes, correct: bytes) -> bool:
    """CMS v1.5.1's line-by-line white-diff, as a reference for small random inputs."""

    def canonical(line: bytes) -> bytes:
        for char in b"\t\n\x0b\x0c\r":
            line = line.replace(bytes([char]), b" ")
        return b" ".join(part for part in line.split(b" ") if part)

    out_file, correct_file = io.BytesIO(output), io.BytesIO(correct)
    while True:
        out_line, correct_line = out_file.readline(), correct_file.readline()
        if not out_line and not correct_line:
            return True
        if canonical(out_line) != canonical(correct_line):
            return False


@pytest.mark.parametrize("chunk_bytes", [1, 2, 3, 7])
def test_random_outputs_agree_with_cms_for_any_chunk_size(
    chunk_bytes: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whitediff, "CHUNK_BYTES", chunk_bytes)
    rng = random.Random(chunk_bytes)  # noqa: S311 - reproducible test data, not secrets
    alphabet = [b"a", b"b", b" ", b"\t", b"\n", b"\r", b"\x0b", b"\x0c"]
    for _ in range(3000):
        output = b"".join(rng.choices(alphabet, k=rng.randint(0, 12)))
        correct = b"".join(rng.choices(alphabet, k=rng.randint(0, 12)))
        expected = _cms_white_diff(output, correct)
        assert white_diff(io.BytesIO(output), io.BytesIO(correct)) is expected, (output, correct)
