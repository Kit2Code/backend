"""Recording imported problem versions in the database."""

import json

from django.db import transaction

from grader_common.importer.pipeline import ImportResult
from grader_common.importer.store import REPORT_PATH
from problems.models import Problem, ProblemVersion, Tag


@transaction.atomic
def register(result: ImportResult, *, make_visible: bool = False) -> ProblemVersion:
    """Create (or find) the Problem and ProblemVersion rows for an import, and make it current.

    Titles and tags are copied from `problem.yaml`, so the problem list needs no file reads.
    """
    fmt = result.problem
    report = json.loads((result.version_dir / REPORT_PATH).read_text(encoding="utf-8"))
    # select_for_update: two imports of the same problem at once wait for each other here.
    problem, _created = Problem.objects.select_for_update().get_or_create(slug=fmt.slug)
    version, _created = ProblemVersion.objects.get_or_create(
        problem=problem,
        dir_name=result.version_dir.name,
        defaults={"content_hash": report["content_sha256"], "report": report},
    )
    problem.title_th = fmt.title.get("th", "")
    problem.title_en = fmt.title.get("en", "")
    problem.current_version = version
    if make_visible:
        problem.is_visible = True
    problem.save()
    problem.tags.set([Tag.objects.get_or_create(name=name)[0] for name in fmt.tags])
    return version
