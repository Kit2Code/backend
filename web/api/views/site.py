"""`GET /api/site`: the home page's summary."""

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api.language import api_language
from api.serializers.site import SiteSerializer
from core.models import SiteSetting
from judging.internal import grading_paused
from problems.views import recent_problems, visible_problems


class SiteView(APIView):
    def get(self, request: Request) -> Response:
        setting = SiteSetting.get()
        data = {
            "announcement": setting.announcement,
            "problem_count": visible_problems().count(),
            "registration_open": setting.registration_open,
            "grading_paused": grading_paused(),
            "recent_problems": recent_problems(),
        }
        context = {"language": api_language(request)}
        return Response(SiteSerializer(data, context=context).data)
