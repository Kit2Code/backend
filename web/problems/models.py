"""Problems, their immutable versions in the problem store, and tags."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class Tag(models.Model):
    # Free text as in problem.yaml (it may be Thai), not a slug.
    name = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        return self.name


class Problem(models.Model):
    """A problem as students see it. Its files live in the store, in `current_version`'s folder."""

    slug = models.SlugField(max_length=64, unique=True)
    title_th = models.CharField(max_length=200, blank=True)
    title_en = models.CharField(max_length=200, blank=True)
    # New problems stay hidden until an admin publishes them.
    is_visible = models.BooleanField(_("visible"), default=False)
    current_version = models.ForeignKey(
        "ProblemVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    tags = models.ManyToManyField(Tag, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:
        return self.slug

    def title_in(self, language: str) -> str:
        """The title in `language`, or in the other language, or the slug."""
        preferred = self.title_en if language == "en" else self.title_th
        return preferred or self.title_th or self.title_en or self.slug


class ProblemVersion(models.Model):
    """One immutable version folder `<store>/<slug>/<dir_name>/`."""

    class Status(models.TextChoices):
        IMPORTED = "imported", _("imported")
        VALIDATING = "validating", _("validating")
        VALID = "valid", _("valid")
        INVALID = "invalid", _("invalid")

    # PROTECT: submissions point at versions, so a problem with history can't vanish by accident.
    problem = models.ForeignKey(Problem, on_delete=models.PROTECT, related_name="versions")
    dir_name = models.CharField(max_length=80)
    content_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IMPORTED)
    report = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["problem", "dir_name"], name="problemversion_dir_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.problem.slug}/{self.dir_name}"
