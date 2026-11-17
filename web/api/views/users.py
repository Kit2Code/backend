"""`GET /api/ranking` and `GET /api/users/{username}` (contract §10)."""

from django.shortcuts import get_object_or_404
from django.utils.translation import get_language
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from api import schema
from api.pagination import RankingPage
from api.serializers.users import photo_url
from api.views.archive import _folders
from api.views.problems import signed_in_user
from judging.models import UserProblemBest
from judging.profile import ranking_rows, solved_and_tried, standing, title_for
from problems import archive
from problems.models import ProblemGroup
from problems.views import visible_problems


class RankingView(APIView):
    @extend_schema(
        operation_id="ranking", parameters=[schema.LANG, schema.PAGE], responses=schema.RankingPage
    )
    def get(self, request: Request) -> Response:
        paginator = RankingPage()
        rows = paginator.paginate_queryset(ranking_rows(), request)
        first_rank = (paginator.page - 1) * paginator.page_size + 1
        results = [
            {
                "rank": first_rank + i,
                "username": row["user__username"],
                "display_name": row["user__display_name"],
                "photo_url": photo_url(
                    row["user__username"], row["user__photo_updated_at"], row["user__show_photo"]
                ),
                "total": row["total"],
            }
            for i, row in enumerate(rows)
        ]
        return paginator.get_paginated_response(results)


def _bests(rows: list[UserProblemBest], language: str) -> list[dict[str, object]]:
    when = serializers.DateTimeField()
    return [
        {
            "slug": row.problem.slug,
            "title": row.problem.title_in(language),
            "best_score": row.best_score,
            "reached_at": when.to_representation(row.reached_at),
        }
        for row in rows
    ]


class ProfileView(APIView):
    @extend_schema(operation_id="user_profile", parameters=[schema.LANG], responses=schema.Profile)
    def get(self, request: Request, username: str) -> Response:
        person = get_object_or_404(User, username=username, is_active=True)
        language = get_language()
        solved, tried = solved_and_tried(person)
        title = title_for(len(solved))
        place = standing(person)
        groups = list(ProblemGroup.objects.all())
        counts = archive.count_by_group(visible_problems(), person)
        cards = archive.folders(
            [g for g in groups if g.parent_id is None],
            groups,
            counts,
            url_prefix="",
            language=language,
            signed_in=True,
        )
        viewer = signed_in_user(request)
        return Response(
            {
                "username": person.username,
                "display_name": person.display_name,
                "photo_url": photo_url(person.username, person.photo_updated_at, person.show_photo),
                "title": {"key": title.key, "name": str(title.name)},
                "standing": {"total": place.total, "rank": place.rank},
                "solved": _bests(solved, language),
                "attempted": _bests(tried, language),
                "folders": _folders(cards),
                "is_self": viewer is not None and viewer.pk == person.pk,
            }
        )
