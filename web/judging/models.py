"""Submissions, their per-test results, the judge queue, workers and the ranking table."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.validators import MaxUtf8BytesValidator
from problems.models import Problem, ProblemVersion

MAX_SOURCE_BYTES = 64 * 1024
MAX_COMPILE_LOG_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 4096


class Verdict(models.TextChoices):
    ACCEPTED = "AC", _("Accepted")
    PARTIAL = "PA", _("Partially correct")
    WRONG = "WA", _("Wrong answer")
    TIME_LIMIT = "TLE", _("Time limit exceeded")
    MEMORY_LIMIT = "MLE", _("Memory limit exceeded")
    RUNTIME_ERROR = "RE", _("Runtime error")
    OUTPUT_LIMIT = "OLE", _("Output limit exceeded")
    COMPILE_ERROR = "CE", _("Compilation error")
    JUDGE_ERROR = "JE", _("Judge error")


class Submission(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", _("queued")
        JUDGING = "judging", _("judging")
        DONE = "done", _("done")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    problem = models.ForeignKey(Problem, on_delete=models.PROTECT)
    problem_version = models.ForeignKey(ProblemVersion, on_delete=models.PROTECT)
    language = models.CharField(max_length=16)
    source = models.TextField(validators=[MaxUtf8BytesValidator(MAX_SOURCE_BYTES)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    verdict = models.CharField(max_length=3, choices=Verdict.choices, blank=True)
    score = models.FloatField(null=True, blank=True)
    subtask_scores = models.JSONField(default=list, blank=True)
    max_time_ms = models.PositiveIntegerField(null=True, blank=True)
    max_memory_kb = models.PositiveIntegerField(null=True, blank=True)
    compile_log = models.TextField(
        blank=True, validators=[MaxUtf8BytesValidator(MAX_COMPILE_LOG_BYTES)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    judged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            # "My submissions" and the per-user submit limits: newest first for one user.
            models.Index(fields=["user", "-created_at"], name="submission_user_idx"),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.problem.slug}"


class TestResult(models.Model):
    __test__ = False  # not a pytest test class, despite the name

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="tests")
    name = models.CharField(max_length=64)
    verdict = models.CharField(max_length=3, choices=Verdict.choices)
    outcome = models.FloatField()
    time_ms = models.PositiveIntegerField()
    memory_kb = models.PositiveIntegerField()
    message = models.CharField(max_length=MAX_MESSAGE_CHARS, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["submission", "name"], name="testresult_name_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.submission_id}/{self.name}: {self.verdict}"


class JudgeJob(models.Model):
    """One unit of work for a worker. Workers claim the highest priority, oldest queued job."""

    class Kind(models.TextChoices):
        JUDGE = "judge", _("judge")
        VALIDATE = "validate", _("validate")
        CALIBRATE = "calibrate", _("calibrate")
        SELFTEST = "selftest", _("self-test")

    class Status(models.TextChoices):
        QUEUED = "queued", _("queued")
        RUNNING = "running", _("running")
        DONE = "done", _("done")
        FAILED = "failed", _("failed")

    class Priority(models.IntegerChoices):
        """Higher runs first."""

        FRESH = 40, _("fresh submission")
        VALIDATION = 30, _("validation")
        REJUDGE = 20, _("rejudge")
        BACKGROUND = 10, _("calibration and self-test")

    kind = models.CharField(max_length=16, choices=Kind.choices)
    priority = models.SmallIntegerField(choices=Priority.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    worker_id = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, null=True, blank=True)
    problem_version = models.ForeignKey(
        ProblemVersion, on_delete=models.CASCADE, null=True, blank=True
    )
    error = models.CharField(max_length=MAX_MESSAGE_CHARS, blank=True)
    # What a job that isn't a submission found (a grader_common.api report, as JSON).
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # The claim: queued jobs, highest priority first, then oldest. Partial, so the index
            # only holds the (few) queued rows, not the whole history.
            models.Index(
                fields=["-priority", "created_at"],
                condition=Q(status="queued"),
                name="judgejob_claim_idx",
            ),
            # Running jobs whose lease ran out go back to the queue (checked during each claim).
            models.Index(
                fields=["lease_until"],
                condition=Q(status="running"),
                name="judgejob_lease_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} job {self.pk} ({self.status})"


class Worker(models.Model):
    worker_id = models.CharField(max_length=64, unique=True)
    env_report = models.JSONField(default=dict, blank=True)
    slots = models.PositiveSmallIntegerField(default=0)
    busy_slots = models.PositiveSmallIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.worker_id


class UserProblemBest(models.Model):
    """A user's best score on a problem and when they first reached it; the ranking sums these."""

    # No plain index on user: upbest_rank_idx below starts with user and covers the same lookups.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, db_index=False)
    # No plain index on problem either: the unique constraint below starts with problem.
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, db_index=False)
    best_score = models.FloatField()
    reached_at = models.DateTimeField()
    submission = models.ForeignKey(Submission, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["problem", "user"], name="upbest_problem_user_uniq"),
        ]
        indexes = [
            # The ranking groups by user and needs only these two columns: a covering index lets
            # Postgres answer it from the index alone.
            models.Index(
                fields=["user"], include=["best_score", "reached_at"], name="upbest_rank_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} on {self.problem_id}: {self.best_score}"
