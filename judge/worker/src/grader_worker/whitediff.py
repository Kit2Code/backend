"""CMS "white-diff": compare a contestant's output with the correct output, ignoring spacing.

Re-implemented from CMS v1.5.1 `cms/grading/steps/whitediff.py` (`_white_diff_canonicalize` and
`_white_diff`); golden tests in tests/unit/golden/whitediff.json were produced by running that
CMS code. The rules:

- The files are compared line by line (a line ends at "\\n"). Line breaks matter.
- Within a line, leading and trailing whitespace is ignored and every run of whitespace counts as
  one space. Whitespace means the ASCII characters space, \\t, \\n, \\v, \\f and \\r only.
- When one file ends, the rest of the other may only contain whitespace (trailing blank lines are
  fine).

CMS reads whole lines and splits them into tokens. A contestant controls the output, so one 64 MB
line of "1 1 1 ..." would make that cost gigabytes in the worker, outside any sandbox. Instead,
each file is read in fixed-size chunks and turned into its "canonical stream": every line written
the CMS way (stripped, whitespace runs as one space) followed by "\\n". Two files are equal under
the rules above exactly when one canonical stream is the other plus some extra "\\n"s (the blank
lines left over when one file ends), so the two streams are compared chunk by chunk. Memory use
stays at a few chunks whatever the output looks like.
"""

import re
from collections.abc import Iterator
from typing import IO

WHITESPACE = b" \t\n\x0b\x0c\r"
CHUNK_BYTES = 64 * 1024

# Turns every whitespace character except the line break into a space.
_TO_SPACE = bytes.maketrans(b"\t\x0b\x0c\r", b"    ")
_SPACE_RUN = re.compile(rb" {2,}")
_SPACE_AROUND_NEWLINE = re.compile(rb" ?\n ?")


def white_diff(output: IO[bytes], correct: IO[bytes]) -> bool:
    """True if the two files are equal under white-diff. Reads them a chunk at a time."""
    return _streams_equal(_canonical_stream(output), _canonical_stream(correct))


def _canonical_stream(file: IO[bytes]) -> Iterator[bytes]:
    """Yield the file's canonical stream in pieces: each line canonicalised, then "\\n".

    The last line gets no "\\n" when the file doesn't end with one; that doesn't matter, because
    trailing "\\n"s are ignored by the comparison. Never yields an empty piece.
    """
    line_has_token = False  # a token of the current line has been yielded already
    pending_space = False  # whitespace was seen after the last token yielded
    while chunk := file.read(CHUNK_BYTES):
        text = chunk.translate(_TO_SPACE)
        body = text.strip(b" ")
        if not body:
            pending_space = True
            continue
        if text[0] == 0x20:  # starts with a space
            pending_space = True
        body = _SPACE_AROUND_NEWLINE.sub(b"\n", _SPACE_RUN.sub(b" ", body))
        # A space between two tokens that straddle the chunk boundary (never at a line's edge).
        if pending_space and line_has_token and body[0] != 0x0A:
            yield b" "
        yield body
        last_newline = body.rfind(b"\n")
        # The body never ends with a space, so after its last "\n" there are only tokens.
        line_has_token = last_newline == -1 or last_newline < len(body) - 1
        pending_space = text[-1] == 0x20


def _streams_equal(first: Iterator[bytes], second: Iterator[bytes]) -> bool:
    """True if one stream is the other followed only by "\\n"s."""
    a = b = b""
    while True:
        a = a or next(first, b"")
        b = b or next(second, b"")
        if not a or not b:
            rest, stream = (b, second) if not a else (a, first)
            return _only_newlines(rest, stream)
        size = min(len(a), len(b))
        if a[:size] != b[:size]:
            return False
        a, b = a[size:], b[size:]


def _only_newlines(rest: bytes, stream: Iterator[bytes]) -> bool:
    if rest.strip(b"\n"):
        return False
    return all(not piece.strip(b"\n") for piece in stream)
