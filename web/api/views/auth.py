"""`GET /api/auth/me`: who is signed in, plus the CSRF token for unsafe requests."""

from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from api import schema
from api.serializers.users import UserSerializer


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    @extend_schema(operation_id="auth_me", responses=schema.Me)
    def get(self, request: Request) -> Response:
        user = request.user if isinstance(request.user, User) else None
        return Response(
            {
                "user": UserSerializer(user).data if user is not None else None,
                "csrf_token": get_token(request._request),
            }
        )
