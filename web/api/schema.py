"""What docs/openapi.yaml says about each endpoint (drf-spectacular). Schema only: the views
build their answers themselves; web/tests/test_openapi.py checks real answers match these shapes.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers as s

from api.serializers.problems import ProblemRowSerializer
from api.serializers.submissions import SubmissionRowSerializer
from api.serializers.users import UserSerializer


class SessionCookie(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "api.auth.StrictSessionAuthentication"
    name = "session"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {"type": "apiKey", "in": "cookie", "name": "g_sess"}


LANG = OpenApiParameter("lang", str, enum=["th", "en"], description="Default th.")
PAGE = OpenApiParameter("page", int, description="Positive integer; default 1.")


class _Page(s.Serializer):
    count = s.IntegerField()
    page = s.IntegerField()
    page_size = s.IntegerField(help_text="50; ranking 100")


class ProblemRow(ProblemRowSerializer):
    my_best = s.FloatField(allow_null=True, required=False, help_text="Signed in only.")
    status = s.ChoiceField(
        ["solved", "tried", "untried"], required=False, help_text="Signed in only."
    )


class Counts(s.Serializer):
    all = s.IntegerField()
    solved = s.IntegerField(allow_null=True)
    tried = s.IntegerField(allow_null=True)
    untried = s.IntegerField(allow_null=True)
    today = s.IntegerField(allow_null=True)


class ProblemPage(_Page):
    results = ProblemRow(many=True)


class ProblemList(ProblemPage):
    counts = Counts(help_text="Signed out: only `all` is set.")


class Language(s.Serializer):
    code = s.CharField()
    name = s.CharField()


class Subtask(s.Serializer):
    name = s.CharField()
    points = s.FloatField()


class Statement(s.Serializer):
    language = s.ChoiceField(["th", "en"])
    html = s.CharField(help_text="Sanitized HTML; empty when only PDFs exist.")
    pdfs = s.DictField(child=s.CharField(), help_text="Language → URL with ?v=.")


class Sample(s.Serializer):
    name = s.CharField()
    input = s.CharField()
    output = s.CharField()
    cut_short = s.BooleanField()


class Attachment(s.Serializer):
    name = s.CharField()
    url = s.CharField()


class ProblemDetail(s.Serializer):
    id = s.IntegerField()
    slug = s.CharField()
    title = s.CharField()
    tags = s.ListField(child=s.CharField())
    max_score = s.FloatField()
    time_limit_ms = s.IntegerField()
    memory_limit_mb = s.IntegerField()
    languages = Language(many=True)
    subtasks = Subtask(many=True)
    statement = Statement()
    samples = Sample(many=True)
    attachments = Attachment(many=True)


class Folder(s.Serializer):
    id = s.IntegerField(allow_null=True, help_text="null for `other`.")
    slug = s.CharField()
    name = s.CharField()
    total = s.IntegerField()
    solved = s.IntegerField(allow_null=True, help_text="null when signed out.")


class GroupList(s.Serializer):
    folders = Folder(many=True)


class GroupRef(s.Serializer):
    id = s.IntegerField(allow_null=True)
    slug = s.CharField()
    name = s.CharField()


class Crumb(s.Serializer):
    slug = s.CharField()
    name = s.CharField()


class GroupDetail(s.Serializer):
    group = GroupRef()
    crumbs = Crumb(many=True)
    folders = Folder(many=True)
    problems = ProblemPage(allow_null=True)


class SubmissionPage(_Page):
    results = SubmissionRowSerializer(many=True)


class Me(s.Serializer):
    user = UserSerializer(allow_null=True)
    csrf_token = s.CharField(help_text="Send it back as X-CSRFToken on unsafe requests.")


class UserEnvelope(s.Serializer):
    user = UserSerializer()


class Register(s.Serializer):
    username = s.CharField(help_text="3–20 of a-z, 0-9, _.")
    display_name = s.CharField(max_length=100)
    accept_privacy = s.BooleanField()


class MyAccount(s.Serializer):
    display_name = s.CharField(max_length=100, required=False)
    show_photo = s.BooleanField(required=False)


class SubmitJson(s.Serializer):
    language = s.CharField()
    source = s.CharField()


class SubmitFile(s.Serializer):
    language = s.CharField()
    file = s.FileField(help_text="Wins over `source`.")


class Submitted(s.Serializer):
    id = s.IntegerField()
    status = s.CharField()


class RankingRow(s.Serializer):
    rank = s.IntegerField()
    username = s.CharField()
    display_name = s.CharField()
    photo_url = s.CharField(allow_null=True)
    total = s.FloatField()


class RankingPage(_Page):
    results = RankingRow(many=True)


class Title(s.Serializer):
    key = s.ChoiceField(["newbie", "pupil", "specialist", "expert", "master"])
    name = s.CharField()


class Standing(s.Serializer):
    total = s.FloatField()
    rank = s.IntegerField(allow_null=True)


class Best(s.Serializer):
    slug = s.CharField()
    title = s.CharField()
    best_score = s.FloatField()
    reached_at = s.DateTimeField()


class Profile(s.Serializer):
    username = s.CharField()
    display_name = s.CharField()
    photo_url = s.CharField(allow_null=True)
    title = Title()
    standing = Standing()
    solved = Best(many=True)
    attempted = Best(many=True)
    folders = Folder(many=True)
    is_self = s.BooleanField()
