"""Submissions (contract §9). Field values follow the contract's per-state table even when the
database disagrees (PLAN.md §5.3, B-5), and measurements are rounded here, not in a template."""

from typing import Any

from rest_framework import serializers

from judging.models import Submission

DONE = Submission.Status.DONE


def _done(submission: Submission) -> bool:
    return submission.status == DONE


def max_time_ms(submission: Submission) -> int | None:
    # complete() stores 0 when no test ran (CE); memory is never 0 once a test ran.
    if not _done(submission) or not submission.max_memory_kb:
        return None
    return round(submission.max_time_ms or 0, -1)


def max_memory_mb(submission: Submission) -> float | None:
    if not _done(submission) or not submission.max_memory_kb:
        return None
    return round(submission.max_memory_kb / 1024, 1)


class SubmissionRowSerializer(serializers.Serializer[Submission]):
    """A list row: never the source or the compiler output."""

    id = serializers.IntegerField()
    problem = serializers.SerializerMethodField()
    user = serializers.SerializerMethodField()
    language = serializers.CharField()
    status = serializers.CharField()
    verdict = serializers.SerializerMethodField()
    score = serializers.SerializerMethodField()
    max_time_ms = serializers.SerializerMethodField()
    max_memory_mb = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_problem(self, submission: Submission) -> dict[str, str]:
        problem = submission.problem
        return {"slug": problem.slug, "title": problem.title_in(self.context["language"])}

    def get_user(self, submission: Submission) -> dict[str, str]:
        return {"username": submission.user.username, "display_name": submission.user.display_name}

    def get_verdict(self, submission: Submission) -> str:
        return submission.verdict if _done(submission) else ""

    def get_score(self, submission: Submission) -> float | None:
        return submission.score if _done(submission) else None

    def get_max_time_ms(self, submission: Submission) -> int | None:
        return max_time_ms(submission)

    def get_max_memory_mb(self, submission: Submission) -> float | None:
        return max_memory_mb(submission)


class SubmissionSerializer(SubmissionRowSerializer):
    """The detail. Context: `language`, `viewer_id` (the signed-in user's id or None)."""

    judged_at = serializers.SerializerMethodField()
    max_score = serializers.SerializerMethodField()
    subtasks = serializers.SerializerMethodField()
    tests = serializers.SerializerMethodField()
    compile_log = serializers.SerializerMethodField()
    is_author = serializers.SerializerMethodField()

    def get_judged_at(self, submission: Submission) -> str | None:
        if not _done(submission) or submission.judged_at is None:
            return None
        return serializers.DateTimeField().to_representation(submission.judged_at)  # type: ignore[no-any-return]

    def get_max_score(self, submission: Submission) -> float | None:
        # Until it is judged, the problem's current maximum is the best guess.
        if submission.max_score is not None:
            return submission.max_score
        return submission.problem.max_score

    def get_subtasks(self, submission: Submission) -> list[dict[str, Any]]:
        return list(submission.subtask_scores) if _done(submission) else []

    def get_tests(self, submission: Submission) -> list[dict[str, Any]]:
        if submission.status == Submission.Status.QUEUED:
            return []
        return [
            {
                "name": t.name,
                "verdict": t.verdict,
                "outcome": t.outcome,
                "time_ms": round(t.time_ms, -1),
                "memory_mb": round(t.memory_kb / 1024, 1),
                "message": t.message,
            }
            for t in submission.tests.order_by("name")
        ]

    def get_compile_log(self, submission: Submission) -> str | None:
        # It may quote source lines: the author only, and only for CE.
        if _done(submission) and submission.verdict == "CE" and self.get_is_author(submission):
            return submission.compile_log
        return None

    def get_is_author(self, submission: Submission) -> bool:
        return bool(submission.user_id == self.context["viewer_id"])
