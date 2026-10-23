"""Recording imported problem versions in the database."""

import json

from django.db import transaction

from grader_common.importer.pipeline import ImportResult
from grader_common.importer.store import REPORT_PATH
from grader_common.problem_format import Problem as ProblemFormat
from problems.models import Problem, ProblemVersion, Tag


@transaction.atomic
def register(
    result: ImportResult, *, make_visible: bool = False, replace_published: bool = True
) -> ProblemVersion:
    """Create (or find) the Problem and ProblemVersion rows for an import, and make it current.

    With `replace_published=False`, a problem that is already public keeps its current version
    (and titles and tags) until the new one is validated and published. Titles and tags are
    copied from `problem.yaml`, so the problem list needs no file reads.
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
    if problem.is_visible and not replace_published:
        return version
    make_current(problem, version, fmt)
    if make_visible:
        problem.is_visible = True
        problem.save(update_fields=["is_visible"])
    return version


def make_current(problem: Problem, version: ProblemVersion, fmt: ProblemFormat) -> None:
    """Point `problem` at `version`, with the titles, tags, maximum score and limits from that
    version's problem.yaml."""
    problem.title_th = fmt.title.get("th", "")
    problem.title_en = fmt.title.get("en", "")
    problem.max_score = fmt.max_score()
    problem.time_limit_ms = fmt.time_limit_ms
    problem.memory_limit_mb = fmt.memory_limit_mb
    problem.current_version = version
    problem.save()
    problem.tags.set([Tag.objects.get_or_create(name=name)[0] for name in fmt.tags])
