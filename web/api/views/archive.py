"""`GET /api/groups[/{slug}[/{sub_slug}]]` (contract §8): the Archive's folders and problems."""

from typing import Any

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api import schema
from api.views.problems import problem_page, signed_in_user
from problems import archive
from problems.models import ProblemGroup
from problems.views import visible_problems


def _folders(cards: list[archive.Folder]) -> list[dict[str, Any]]:
    return [
        {"id": c.id, "slug": c.slug, "name": c.name, "total": c.total, "solved": c.solved}
        for c in cards
    ]


class GroupListView(APIView):
    @extend_schema(operation_id="groups_list", parameters=[schema.LANG], responses=schema.GroupList)
    def get(self, request: Request) -> Response:
        user = signed_in_user(request)
        groups = list(ProblemGroup.objects.all())
        counts = archive.count_by_group(visible_problems(), user)
        cards = archive.folders(
            [g for g in groups if g.parent_id is None],
            groups,
            counts,
            url_prefix="",
            language=get_language(),
            signed_in=user is not None,
        )
        other_total, other_solved = counts.get(None, (0, 0))
        if other_total:
            cards.append(
                archive.Folder(
                    id=None,
                    slug=ProblemGroup.UNGROUPED_SLUG,
                    url="",
                    name=_("Other"),
                    total=other_total,
                    solved=other_solved if user is not None else None,
                )
            )
        return Response({"folders": _folders(cards)})


class GroupDetailView(APIView):
    @extend_schema(
        operation_id="group_detail",
        parameters=[
            schema.LANG,
            schema.PAGE,
            OpenApiParameter("status", str),
            OpenApiParameter("sort", str),
        ],
        responses=schema.GroupDetail,
    )
    def get(self, request: Request, slug: str, sub_slug: str = "") -> Response:
        language = get_language()
        problems = visible_problems()
        if slug == ProblemGroup.UNGROUPED_SLUG and not sub_slug:
            ungrouped = problems.filter(group__isnull=True)
            if not ungrouped.exists():
                raise Http404
            return Response(
                {
                    "group": {"id": None, "slug": slug, "name": _("Other")},
                    "crumbs": [],
                    "folders": [],
                    "problems": problem_page(request, ungrouped),
                }
            )
        top = get_object_or_404(ProblemGroup, slug=slug, parent__isnull=True)
        group = get_object_or_404(ProblemGroup, slug=sub_slug, parent=top) if sub_slug else top
        user = signed_in_user(request)
        groups = list(ProblemGroup.objects.filter(Q(pk=group.pk) | Q(parent=group)))
        counts = archive.count_by_group(problems.filter(group__in=groups), user)
        if not archive.numbers(group, groups, counts)[0]:
            raise Http404
        cards = archive.folders(
            [g for g in groups if g.parent_id == group.pk],
            groups,
            counts,
            url_prefix="",
            language=language,
            signed_in=user is not None,
        )
        own = problems.filter(group=group) if counts.get(group.pk, (0, 0))[0] else None
        return Response(
            {
                "group": {"id": group.pk, "slug": group.slug, "name": group.name_in(language)},
                "crumbs": [{"slug": top.slug, "name": top.name_in(language)}] if sub_slug else [],
                "folders": _folders(cards),
                "problems": problem_page(request, own) if own is not None else None,
            }
        )


class SubgroupDetailView(GroupDetailView):
    """The same view under its own name, so the schema has one operation per URL."""

    @extend_schema(
        operation_id="subgroup_detail",
        parameters=[
            schema.LANG,
            schema.PAGE,
            OpenApiParameter("status", str),
            OpenApiParameter("sort", str),
        ],
        responses=schema.GroupDetail,
    )
    def get(self, request: Request, slug: str, sub_slug: str = "") -> Response:
        return super().get(request, slug, sub_slug)
