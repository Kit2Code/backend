"""Pages as the contract wants them (contract §11): `?page=` is a positive integer, else 400;
past the end is an empty `results` with the real `count` (stock DRF answers 404 there)."""

import re
from collections.abc import Sequence
from typing import Any

from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import BasePagination
from rest_framework.request import Request
from rest_framework.response import Response


class Page(BasePagination):
    page_size = 50

    def paginate_queryset(
        self, queryset: QuerySet[Any] | Sequence[Any], request: Request, view: Any = None
    ) -> list[Any]:
        raw = request.query_params.get("page", "1")
        if not re.fullmatch(r"[1-9][0-9]*", raw):
            raise ValidationError({"page": ["Page must be a positive whole number."]})
        self.page = int(raw)
        self.count = queryset.count() if isinstance(queryset, QuerySet) else len(queryset)
        start = (self.page - 1) * self.page_size
        if start >= self.count:  # also keeps a huge ?page= out of the SQL OFFSET
            return []
        return list(queryset[start : start + self.page_size])

    def get_paginated_response(self, data: Any) -> Response:
        return Response(
            {"count": self.count, "page": self.page, "page_size": self.page_size, "results": data}
        )


class RankingPage(Page):
    page_size = 100
