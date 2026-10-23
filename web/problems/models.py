"""Problems, their immutable versions in the problem store, and tags."""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class Tag(models.Model):
    # Free text as in problem.yaml (it may be Thai), not a slug.
    name = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        return self.name


class ProblemGroup(models.Model):
    """A folder in the Archive, such as "TOI" or "TOI20" inside it. Two levels at most."""

    # The Archive's URL for problems with no group (/archive/other).
    UNGROUPED_SLUG = "other"

    slug = models.SlugField(max_length=64)
    name_th = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    # Smaller first; groups with the same order go by slug.
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "slug"]
        constraints = [
            # nulls_distinct=False: top-level groups (no parent) need unique slugs too.
            models.UniqueConstraint(
                fields=["parent", "slug"], name="problemgroup_slug_uniq", nulls_distinct=False
            ),
        ]

    def __str__(self) -> str:
        return f"{self.parent.slug}/{self.slug}" if self.parent else self.slug

    def clean(self) -> None:
        """Two levels only: a parent must be top-level; a group with children stays top-level."""
        if self.slug == self.UNGROUPED_SLUG:
            raise ValidationError({"slug": _("This name is kept for problems with no group.")})
        if self.parent is None:
            return
        if self.parent.parent_id is not None or self.parent.pk == self.pk:
            raise ValidationError({"parent": _("Groups go two levels deep at most.")})
        if self.pk is not None and self.children.exists():
            raise ValidationError({"parent": _("A group with subgroups must stay at the top.")})

    def name_in(self, language: str) -> str:
        """The name in `language`, or in the other language."""
        preferred = self.name_en if language == "en" else self.name_th
        return preferred or self.name_th or self.name_en


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
    # Deleting a group moves its problems to "other", it never deletes them.
    group = models.ForeignKey(
        ProblemGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="problems"
    )
    # Copied from the current version's problem.yaml (like the titles), so lists never read files.
    # NULL until a version is made current. "Solved" means a best score of max_score.
    max_score = models.FloatField(null=True, blank=True)
    time_limit_ms = models.PositiveIntegerField(null=True, blank=True)
    memory_limit_mb = models.PositiveIntegerField(null=True, blank=True)
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
