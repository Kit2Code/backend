"""`GET /api/site` (contract §7, "Site summary")."""

from rest_framework import serializers

from problems.models import Problem


class RecentProblemSerializer(serializers.Serializer[Problem]):
    slug = serializers.CharField()
    title = serializers.SerializerMethodField()

    def get_title(self, problem: Problem) -> str:
        return problem.title_in(self.context["language"])


class SiteSerializer(serializers.Serializer[dict[str, object]]):
    announcement = serializers.CharField()
    problem_count = serializers.IntegerField()
    registration_open = serializers.BooleanField()
    grading_paused = serializers.BooleanField()
    recent_problems = RecentProblemSerializer(many=True)
