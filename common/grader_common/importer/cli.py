"""`grader-import`: import a problem (zip or folder) into a problem store from the command line.

    uv run grader-import fixtures/problems/aplusb --store .devdata/problems

On success it prints the version folder on stdout (warnings go to stderr) and exits 0. If the
problem can't be imported it prints one line starting with "grader-import: error:" and exits 1.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import import_problem


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command with `argv` (default: the real command line) and return the exit code."""
    parser = argparse.ArgumentParser(
        prog="grader-import",
        description="Import a problem (native or CMS Italian format, zip or folder).",
    )
    parser.add_argument("source", type=Path, help="the problem's zip file or folder")
    parser.add_argument("--store", type=Path, required=True, help="the problem store folder")
    args = parser.parse_args(argv)

    try:
        result = import_problem(args.source, args.store)
    except ProblemImportError as error:
        print(f"grader-import: error: {error}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not result.created:
        print("grader-import: already imported, nothing changed", file=sys.stderr)
    print(result.version_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
