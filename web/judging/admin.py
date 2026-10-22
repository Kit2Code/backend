"""Submissions on the admin site: read-only, with their code, and a rejudge action (audited)."""

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from config.admin import AuditedModelAdmin
from core import audit
from judging.models import JudgeJob, Submission


@admin.register(Submission)
class SubmissionAdmin(AuditedModelAdmin):
    list_display = ("pk", "user", "problem", "language", "status", "verdict", "score", "created_at")
    list_filter = ("status", "verdict", "language")
    search_fields = ("user__username", "problem__slug")
    list_select_related = ("user", "problem")
    date_hierarchy = "created_at"
    fields = (
        "user", "problem", "problem_version", "language", "status", "verdict", "score",
        "subtask_scores", "max_time_ms", "max_memory_kb", "created_at", "judged_at", "code",
        "compile_log",
    )  # fmt: skip
    readonly_fields = fields
    actions = ("rejudge",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False  # the history (and the ranking) depends on them

    @admin.display(description=_("code"))
    def code(self, submission: Submission) -> SafeString:
        return format_html("<pre><code>{}</code></pre>", submission.source)

    @admin.action(description=_("Rejudge the selected submissions"), permissions=["change"])
    def rejudge(self, request: HttpRequest, queryset: QuerySet[Submission]) -> None:
        queued = []
        with transaction.atomic():
            waiting = JudgeJob.objects.filter(
                status__in=[JudgeJob.Status.QUEUED, JudgeJob.Status.RUNNING],
                submission__in=queryset,
            ).values_list("submission_id", flat=True)
            for submission in queryset.exclude(pk__in=list(waiting)).select_for_update():
                submission.status = Submission.Status.QUEUED
                submission.verdict = ""
                submission.score = None
                submission.save(update_fields=["status", "verdict", "score"])
                JudgeJob.objects.create(
                    kind=JudgeJob.Kind.JUDGE,
                    priority=JudgeJob.Priority.REJUDGE,
                    submission=submission,
                )
                queued.append(submission.pk)
        if queued:
            audit.record(request, "submission.rejudge", "", {"ids": sorted(queued)})
        message = _("%(count)d submission(s) queued for rejudging.") % {"count": len(queued)}
        self.message_user(request, message, messages.SUCCESS)
