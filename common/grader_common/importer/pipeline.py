"""Import one upload (zip or folder, native or CMS Italian format) into the problem store.

Steps: unpack safely into a private staging folder, detect the format, build a version folder in
our native layout, check it, hash it, and then either find the same content already stored or
rename the folder into place as the next version. Any error leaves the store untouched.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grader_common.importer import italy, native, store
from grader_common.importer.archive import (
    DEFAULT_LIMITS,
    Limits,
    copy_tree,
    extract_zip,
    unwrap_single_folder,
)
from grader_common.importer.errors import ProblemImportError
from grader_common.problem_format import Problem


@dataclass(frozen=True)
class ImportResult:
    """What an import produced."""

    version_dir: Path
    created: bool  # False when the same content was already stored
    problem: Problem
    test_names: list[str]
    warnings: list[str]


def import_problem(source: Path, store_dir: Path, limits: Limits = DEFAULT_LIMITS) -> ImportResult:
    """Import the zip file or folder `source` into `store_dir`."""
    with store.staging_area(store_dir) as staging:
        upload = staging / "upload"
        if source.is_file():
            extract_zip(source, upload, limits)
        elif source.is_dir():
            copy_tree(source, upload, limits)
        else:
            raise ProblemImportError(f"{source}: no such file or folder")
        root = unwrap_single_folder(upload)

        version = staging / "version"
        if (root / "problem.yaml").is_file():
            source_format = "native"
            warnings = native.assemble(root, version)
        elif (root / "task.yaml").is_file():
            source_format = "cms-italian"
            warnings = italy.convert(root, version)
        else:
            raise ProblemImportError(
                "no problem.yaml (native format) or task.yaml (CMS Italian format) found"
            )

        checked = native.check_version(version)
        digest = store.content_hash(version)
        report = {
            "format": source_format,
            "source": source.name,
            "content_sha256": digest,
            "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "test_count": len(checked.test_names),
            "warnings": warnings,
        }
        (version / "meta").mkdir(exist_ok=True)
        (version / store.REPORT_PATH).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        slug = checked.problem.slug
        existing = store.find_existing(store_dir, slug, digest)
        if existing is not None:
            version_dir, created = existing, False
        else:
            version_dir, created = store.store_version(version, store_dir, slug, digest), True
        return ImportResult(
            version_dir=version_dir,
            created=created,
            problem=checked.problem,
            test_names=checked.test_names,
            warnings=warnings,
        )
