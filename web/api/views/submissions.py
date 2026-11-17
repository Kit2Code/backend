"""Submissions (contract §9): detail (polled while judging), lists, and the author's source."""

from django.db.models import QuerySet
from django.utils.translation import get_language
from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import Page
from api.serializers.submissions import SubmissionRowSerializer, SubmissionSerializer
from api.views.problems import signed_in_user
from judging.forms import SubmissionFilterForm
from judging.models import Submission
from judging.views import _listed, _visible_submission


class SubmissionDetailView(APIView):
    def get(self, request: Request, pk: int) -> Response:
        submission = _visible_submission(pk)  # 404 unless the problem is visible
        user = signed_in_user(request)
        context = {"language": get_language(), "viewer_id": user.pk if user else None}
        return Response(SubmissionSerializer(submission, context=context).data)


def _filtered(
    request: Request, submissions: QuerySet[Submission], *fields: str
) -> QuerySet[Submission]:
    """Apply the `fields` filters from the query; a bad value is 400 (contract §9)."""
    form = SubmissionFilterForm({f: request.query_params.get(f, "") for f in fields})
    if not form.is_valid():
        raise ValidationError(form.errors)
    data = form.cleaned_data
    if data.get("problem"):
        submissions = submissions.filter(problem__slug=data["problem"])
    if data.get("user"):
        submissions = submissions.filter(user__username=data["user"])
    if data.get("verdict"):
        submissions = submissions.filter(verdict=data["verdict"], status=Submission.Status.DONE)
    return submissions


def _page(request: Request, submissions: QuerySet[Submission]) -> Response:
    paginator = Page()
    rows = paginator.paginate_queryset(submissions.order_by("-pk"), request)
    context = {"language": get_language()}
    return paginator.get_paginated_response(
        SubmissionRowSerializer(rows, many=True, context=context).data
    )


class SubmissionListView(APIView):
    def get(self, request: Request) -> Response:
        return _page(request, _filtered(request, _listed(), "problem", "user", "verdict"))


class MySubmissionListView(APIView):
    def get(self, request: Request) -> Response:
        user = signed_in_user(request)
        if user is None:
            raise NotAuthenticated
        return _page(request, _filtered(request, _listed().filter(user=user), "problem"))
