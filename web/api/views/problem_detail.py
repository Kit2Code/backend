"""`GET /api/problems/{slug}` (contract §7, "Problem detail"). Hidden problems are 404."""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import get_language
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from grader_common.problem_format import LANGUAGE_NAMES, GroupMinScoring
from problems import files, store
from problems.statements import statement
from problems.views import _samples, visible_problems


class ProblemDetailView(APIView):
    def get(self, request: Request, slug: str) -> Response:
        problem = get_object_or_404(visible_problems().select_related("current_version"), slug=slug)
        version = problem.current_version
        if version is None:  # excluded by visible_problems(); this check is for the type checker
            raise Http404
        language = get_language()
        fmt = store.problem_format(version)
        text = statement(version, language)

        def url(name: str) -> str:
            return reverse("api-problem-file", args=[slug, name])

        return Response(
            {
                "id": problem.pk,
                "slug": problem.slug,
                "title": problem.title_in(language),
                "tags": [tag.name for tag in problem.tags.all()],
                "max_score": problem.max_score,
                "time_limit_ms": problem.time_limit_ms,
                "memory_limit_mb": problem.memory_limit_mb,
                "languages": [{"code": c, "name": LANGUAGE_NAMES[c]} for c in fmt.languages],
                "subtasks": [{"name": s.name, "points": s.points} for s in fmt.scoring.subtasks]
                if isinstance(fmt.scoring, GroupMinScoring)
                else [],
                "statement": {
                    "language": text.language,
                    "html": text.html,  # Markdown without raw HTML, then allow-list sanitized
                    # ?v= changes with the version, so a new PDF is never served from a cache.
                    "pdfs": {
                        lang: f"{url(name)}?v={version.pk}" for lang, name in text.pdfs.items()
                    },
                },
                "samples": [vars(sample) for sample in _samples(version)],
                "attachments": [
                    {"name": name, "url": url(name)} for name in files.attachments(version)
                ],
            }
        )
