"""`POST /api/problems/{slug}/submissions` (contract §9): JSON or a file upload."""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotAuthenticated, Throttled, ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api import schema
from api.views.problems import signed_in_user
from judging import services
from judging.forms import SubmitForm
from problems import store
from problems.views import visible_problems


class SubmitView(APIView):
    parser_classes = [JSONParser, MultiPartParser]  # ApiMiddleware caps the body at 256 KB first

    @extend_schema(
        operation_id="submit",
        request={"application/json": schema.SubmitJson, "multipart/form-data": schema.SubmitFile},
        responses={201: schema.Submitted},
    )
    def post(self, request: Request, slug: str) -> Response:
        user = signed_in_user(request)
        if user is None:
            raise NotAuthenticated
        problem = get_object_or_404(visible_problems().select_related("current_version"), slug=slug)
        version = problem.current_version
        if version is None:  # excluded by visible_problems(); this check is for the type checker
            raise Http404
        data = request.data if isinstance(request.data, dict) else {}
        fields = {name: data[name] for name in ("language", "source") if name in data}
        form = SubmitForm(fields, request.FILES, languages=store.problem_format(version).languages)
        if not form.is_valid():
            errors = dict(form.errors)
            if "__all__" in errors:  # blank, too long, NUL: about the code (PLAN.md B-11)
                errors["source"] = errors.pop("__all__")
            raise ValidationError(errors)
        result = services.create_submission(
            user.pk, problem, version, form.cleaned_data["language"], form.cleaned_data["code"]
        )
        if isinstance(result, services.Refused):
            raise Throttled(wait=result.retry_after, code=result.code)
        location = reverse("api-submission", args=[result.pk])
        return Response(
            {"id": result.pk, "status": result.status}, status=201, headers={"Location": location}
        )
