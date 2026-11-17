"""`GET /api/problems`: the filtered, sorted, paged problem list with the user's counts."""

from dataclasses import asdict
from typing import Any

from django.db.models import QuerySet
from django.utils.translation import get_language
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from api import schema
from api.pagination import Page
from api.serializers.problems import ProblemRowSerializer
from problems import tasks
from problems.models import Problem
from problems.views import MAX_SEARCH_LENGTH, visible_problems


def signed_in_user(request: Request) -> User | None:
    return request.user if isinstance(request.user, User) else None


def problem_page(
    request: Request, problems: QuerySet[Problem], *, search: str = "", group: int | None = None
) -> dict[str, Any]:
    """One page of problem rows: `status`, `sort`, `page` from the query (contract §7, §8)."""
    user = signed_in_user(request)
    sort = request.query_params.get("sort", "id")
    if sort.removeprefix("-") not in tasks.SORT_COLUMNS:
        sort = "id"
    language = get_language()
    rows = tasks.rows(
        problems,
        user,
        search=search,
        group=group,
        status=request.query_params.get("status", "") if user is not None else "",
        sort=sort,
        language=language,
    )
    paginator = Page()
    page = paginator.paginate_queryset(rows, request)
    context = {"language": language, "signed_in": user is not None}
    return {
        "count": paginator.count,
        "page": paginator.page,
        "page_size": paginator.page_size,
        "results": ProblemRowSerializer(page, many=True, context=context).data,
    }


class ProblemListView(APIView):
    @extend_schema(
        operation_id="problems_list",
        parameters=[
            schema.LANG,
            schema.PAGE,
            OpenApiParameter("q", str),
            OpenApiParameter("group", int),
            OpenApiParameter("status", str, enum=["solved", "tried", "untried"]),
            OpenApiParameter("sort", str, description="id, title or passes; - for descending"),
        ],
        responses=schema.ProblemList,
    )
    def get(self, request: Request) -> Response:
        user = signed_in_user(request)
        group_text = request.query_params.get("group", "")
        problems = visible_problems()
        data = problem_page(
            request,
            problems,
            search=request.query_params.get("q", "").strip()[:MAX_SEARCH_LENGTH],
            group=int(group_text) if group_text.isascii() and group_text.isdigit() else None,
        )
        counts = asdict(tasks.counts(problems, user))
        if user is None:  # signed out, only `all` is known (contract §7)
            counts = {**dict.fromkeys(counts), "all": counts["all"]}
        return Response({**data, "counts": counts})
