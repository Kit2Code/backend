"""`GET /api/site`: the home page's summary."""

from django.utils.translation import get_language
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api import schema
from api.serializers.site import SiteSerializer
from core.models import SiteSetting
from judging.internal import grading_paused
from problems.views import recent_problems, visible_problems


class SiteView(APIView):
    @extend_schema(operation_id="site", parameters=[schema.LANG], responses=SiteSerializer)
    def get(self, request: Request) -> Response:
        setting = SiteSetting.get()
        data = {
            "announcement": setting.announcement,
            "problem_count": visible_problems().count(),
            "registration_open": setting.registration_open,
            "grading_paused": grading_paused(),
            "recent_problems": recent_problems(),
        }
        context = {"language": get_language()}
        return Response(SiteSerializer(data, context=context).data)
