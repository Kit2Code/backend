"""`GET /api/problems`: the filtered, sorted, paged problem list with the user's counts."""

from dataclasses import asdict

from django.utils.translation import get_language
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from api.pagination import Page
from api.serializers.problems import ProblemRowSerializer
from problems import tasks
from problems.views import MAX_SEARCH_LENGTH, visible_problems


class ProblemListView(APIView):
    def get(self, request: Request) -> Response:
        user = request.user if isinstance(request.user, User) else None
        params = request.query_params
        sort = params.get("sort", "id")
        if sort.removeprefix("-") not in tasks.SORT_COLUMNS:
            sort = "id"
        group_text = params.get("group", "")
        language = get_language()
        problems = visible_problems()
        rows = tasks.rows(
            problems,
            user,
            search=params.get("q", "").strip()[:MAX_SEARCH_LENGTH],
            group=int(group_text) if group_text.isascii() and group_text.isdigit() else None,
            status=params.get("status", "") if user is not None else "",
            sort=sort,
            language=language,
        )
        paginator = Page()
        page = paginator.paginate_queryset(rows, request)
        counts = tasks.counts(problems, user)
        context = {"language": language, "signed_in": user is not None}
        return Response(
            {
                "count": paginator.count,
                "page": paginator.page,
                "page_size": paginator.page_size,
                # Signed out, only `all` is known (contract §7).
                "counts": asdict(counts)
                if user
                else {**dict.fromkeys(asdict(counts)), "all": counts.all},
                "results": ProblemRowSerializer(page, many=True, context=context).data,
            }
        )
