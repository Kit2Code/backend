"""Problem rows (contract §7, "Problem list")."""

from typing import Any

from rest_framework import serializers

from problems import tasks
from problems.models import Problem


class ProblemRowSerializer(serializers.Serializer[Problem]):
    """`my_best` and `status` only when the context has `signed_in`."""

    id = serializers.IntegerField()
    slug = serializers.CharField()
    title = serializers.SerializerMethodField()
    max_score = serializers.FloatField()
    time_limit_ms = serializers.IntegerField()
    memory_limit_mb = serializers.IntegerField()
    passes = serializers.IntegerField()

    def get_title(self, problem: Problem) -> str:
        return problem.title_in(self.context["language"])

    def to_representation(self, problem: Problem) -> dict[str, Any]:
        data: dict[str, Any] = super().to_representation(problem)
        if self.context["signed_in"]:
            data["my_best"] = problem.my_best  # type: ignore[attr-defined]
            data["status"] = tasks.status_of(problem)
        return data
