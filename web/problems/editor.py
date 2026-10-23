"""Creating and editing a problem in the browser.

The form only collects what problem.yaml and the problem folder need. `build_folder` writes a
native problem folder (docs/PROBLEM_FORMAT.md) into a private temporary folder, and the caller
imports it with the same importer as an uploaded zip: every safety check, the versioning and the
validation queue are the same. Editing starts from a copy of the current version's folder, so
files that aren't uploaded again are kept.
"""

import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from grader_common.importer.archive import extract_zip
from grader_common.importer.errors import ProblemImportError
from grader_common.problem_format import LANGUAGE_NAMES, TEST_NAME_RE
from problems.models import ProblemGroup, ProblemVersion
from problems.store import version_path

SOLUTION_SLOTS = (1, 2, 3)
# Which languages a reference solution's file extension allows.
EXTENSION_LANGUAGES = {".c": {"c11"}, ".cpp": {"cpp17", "cpp20"}, ".py": {"python3"}}
TEST_FILE_RE = re.compile(rf"({TEST_NAME_RE.pattern})\.(in|out)")
STATEMENT_LANGUAGES = ("th", "en")
MAX_PREVIEW_CHARS = 200_000


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Several files from one <input type="file" multiple> (Django's documented recipe)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data: Any, initial: Any = None) -> list["UploadedFile[bytes]"]:
        if isinstance(data, list | tuple):
            return [super(MultipleFileField, self).clean(item, initial) for item in data]
        cleaned = super().clean(data, initial)
        return [cleaned] if cleaned else []


class EditorForm(forms.Form):
    """Everything about a problem that the browser editor can set."""

    slug = forms.RegexField(
        regex=r"^[a-z0-9][a-z0-9_-]{0,63}\Z",
        label=gettext_lazy("Short name (slug)"),
        help_text=gettext_lazy("The web address: a-z, 0-9, _ and -. It can't change later."),
    )
    title_th = forms.CharField(label=gettext_lazy("Title (Thai)"), max_length=200, required=False)
    title_en = forms.CharField(
        label=gettext_lazy("Title (English)"), max_length=200, required=False
    )
    group = forms.ModelChoiceField(
        queryset=ProblemGroup.objects.all(), required=False, label=gettext_lazy("Group")
    )
    tags = forms.CharField(
        label=gettext_lazy("Tags"), required=False, help_text=gettext_lazy("Separated by commas.")
    )
    time_limit_ms = forms.IntegerField(
        label=gettext_lazy("Time limit (ms)"), min_value=1, max_value=60_000
    )
    memory_limit_mb = forms.IntegerField(
        label=gettext_lazy("Memory limit (MB)"), min_value=16, max_value=512
    )
    languages = forms.MultipleChoiceField(
        label=gettext_lazy("Languages"),
        choices=list(LANGUAGE_NAMES.items()),
        widget=forms.CheckboxSelectMultiple,
    )
    statement_th = forms.CharField(
        label=gettext_lazy("Statement in Markdown (Thai)"), widget=forms.Textarea, required=False
    )
    statement_en = forms.CharField(
        label=gettext_lazy("Statement in Markdown (English)"),
        widget=forms.Textarea,
        required=False,
    )
    statement_pdf_th = forms.FileField(label=gettext_lazy("Statement PDF (Thai)"), required=False)
    statement_pdf_en = forms.FileField(
        label=gettext_lazy("Statement PDF (English)"), required=False
    )
    tests = MultipleFileField(
        label=gettext_lazy("Test files"),
        required=False,
        help_text=gettext_lazy("Pick every <name>.in and <name>.out file at once."),
    )
    tests_zip = forms.FileField(
        label=gettext_lazy("…or one zip of the tests"),
        required=False,
        help_text=gettext_lazy("The .in and .out files, at the top or in a tests/ folder."),
    )
    samples = forms.CharField(
        label=gettext_lazy("Sample tests"),
        required=False,
        help_text=gettext_lazy("Test names shown on the problem page, separated by commas."),
    )
    scoring = forms.ChoiceField(
        label=gettext_lazy("Scoring"),
        choices=[
            ("sum", gettext_lazy("Sum: every test is worth the same")),
            ("group_min", gettext_lazy("Subtasks: each scores its points if all its tests pass")),
        ],
    )
    total = forms.FloatField(label=gettext_lazy("Total points"), required=False, min_value=0.001)
    subtasks = forms.CharField(
        label=gettext_lazy("Subtasks"),
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        help_text=gettext_lazy(
            "One subtask per line: its points, then its test names or patterns, "
            'for example "30 1-*" or "70 1-* 2-*".'
        ),
    )
    checker = forms.ChoiceField(
        label=gettext_lazy("Checker"),
        choices=[
            ("diff", gettext_lazy("Compare the output (CMS white-diff)")),
            ("cms", gettext_lazy("CMS-style checker")),
            ("testlib", gettext_lazy("testlib checker")),
        ],
    )
    checker_file = forms.FileField(label=gettext_lazy("checker.cpp"), required=False)

    def __init__(self, *args: Any, editing: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.editing = editing
        if editing:
            self.fields["slug"].disabled = True  # the web address never changes
        language_choices = [("", "—"), *LANGUAGE_NAMES.items()]
        for slot in SOLUTION_SLOTS:
            self.fields[f"solution{slot}"] = forms.FileField(
                label=_("Reference solution %(n)d") % {"n": slot}, required=False
            )
            self.fields[f"solution{slot}_lang"] = forms.ChoiceField(
                label=_("Language"), choices=language_choices, required=False
            )
            self.fields[f"solution{slot}_score"] = forms.FloatField(
                label=_("Expected score"), required=False, min_value=0,
                help_text=_("Empty: run it and report the score only."),
            )  # fmt: skip

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if not cleaned.get("title_th") and not cleaned.get("title_en"):
            self.add_error("title_th", _("Give a title in Thai or English."))
        if not self.editing and not cleaned.get("tests") and not cleaned.get("tests_zip"):
            self.add_error("tests", _("Add the tests: .in and .out files, or a zip."))
        if cleaned.get("tests") and cleaned.get("tests_zip"):
            self.add_error("tests_zip", _("Give either the test files or a zip, not both."))
        if cleaned.get("scoring") == "sum" and not cleaned.get("total"):
            self.add_error("total", _("Sum scoring needs the total points."))
        if cleaned.get("scoring") == "group_min":
            try:
                cleaned["parsed_subtasks"] = parse_subtasks(cleaned.get("subtasks", ""))
            except ValueError as error:
                self.add_error("subtasks", str(error))
        for slot in SOLUTION_SLOTS:
            self._clean_solution(cleaned, slot)
        return cleaned

    def _clean_solution(self, cleaned: dict[str, Any], slot: int) -> None:
        upload: UploadedFile[bytes] | None = cleaned.get(f"solution{slot}")
        language = cleaned.get(f"solution{slot}_lang", "")
        if upload is None:
            return
        allowed = EXTENSION_LANGUAGES.get(Path(upload.name or "").suffix.lower(), set())
        if not allowed:
            self.add_error(f"solution{slot}", _("A solution must be a .c, .cpp or .py file."))
        elif language not in allowed:
            self.add_error(
                f"solution{slot}_lang",
                _("This solution's file doesn't match its language."),
            )


def parse_subtasks(text: str) -> list[dict[str, Any]]:
    """'30 1-*' lines → problem.yaml subtasks. Raises ValueError with a message for the user."""
    subtasks = []
    for number, line in enumerate((ln for ln in text.splitlines() if ln.strip()), start=1):
        points, *patterns = line.split()
        try:
            value = float(points)
        except ValueError:
            value = -1.0
        if value < 0 or not patterns:
            raise ValueError(
                _("Subtask line %(n)d: write the points, then test names or patterns.")
                % {"n": number}
            )
        subtasks.append({"name": str(number), "points": value, "tests": patterns})
    if not subtasks:
        raise ValueError(_("Subtask scoring needs at least one subtask line."))
    return subtasks


def initial_from(version: ProblemVersion) -> dict[str, Any]:
    """The edit form's starting values: the current version's problem.yaml and Markdown."""
    folder = version_path(version)
    meta: dict[str, Any] = yaml.safe_load((folder / "problem.yaml").read_text(encoding="utf-8"))
    scoring = meta["scoring"]
    initial: dict[str, Any] = {
        "slug": meta["slug"],
        "title_th": meta["title"].get("th", ""),
        "title_en": meta["title"].get("en", ""),
        "group": version.problem.group_id,
        "tags": ", ".join(meta.get("tags", [])),
        "time_limit_ms": meta["time_limit_ms"],
        "memory_limit_mb": meta["memory_limit_mb"],
        "languages": meta["languages"],
        "samples": ", ".join(str(name) for name in meta.get("samples", [])),
        "scoring": scoring["type"],
        "total": scoring.get("total"),
        "subtasks": "\n".join(
            " ".join([f"{subtask['points']:g}", *subtask["tests"]])
            for subtask in scoring.get("subtasks", [])
        ),
        "checker": meta.get("checker", {}).get("type", "diff"),
    }
    for language in STATEMENT_LANGUAGES:
        markdown = folder / "statement" / f"{language}.md"
        if markdown.is_file():
            initial[f"statement_{language}"] = markdown.read_text(encoding="utf-8")
    return initial


@contextmanager
def scratch_folder() -> Iterator[Path]:
    """A private temporary folder under the uploads (the same place zip uploads go)."""
    uploads = Path(settings.GRADER_UPLOADS_DIR) / "tmp"
    uploads.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(dir=uploads))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def build_folder(data: dict[str, Any], base: ProblemVersion | None, dest: Path) -> None:
    """Write a native problem folder at `dest` from the form's cleaned `data`.

    `base` is the version being edited (its files are the starting point), or None for a new
    problem. Raises ProblemImportError with a message for the user.
    """
    meta: dict[str, Any] = {"format_version": 1}
    if base is not None:
        shutil.copytree(version_path(base), dest, ignore=shutil.ignore_patterns("meta"))
        meta = yaml.safe_load((dest / "problem.yaml").read_text(encoding="utf-8"))
    else:
        dest.mkdir()
    meta["slug"] = data["slug"]
    meta["title"] = {
        language: data[f"title_{language}"]
        for language in STATEMENT_LANGUAGES
        if data[f"title_{language}"]
    }
    meta["tags"] = [tag.strip() for tag in data.get("tags", "").split(",") if tag.strip()]
    meta["time_limit_ms"] = data["time_limit_ms"]
    meta["memory_limit_mb"] = data["memory_limit_mb"]
    meta["languages"] = list(data["languages"])
    meta["samples"] = [name.strip() for name in data.get("samples", "").split(",") if name.strip()]
    if data["scoring"] == "sum":
        meta["scoring"] = {"type": "sum", "total": data["total"]}
    else:
        meta["scoring"] = {"type": "group_min", "subtasks": data["parsed_subtasks"]}
    _write_statements(data, dest)
    _write_tests(data, dest)
    meta["checker"] = _write_checker(data, dest)
    solutions = _write_solutions(data, dest)
    if solutions is not None:
        meta["solutions"] = solutions
    meta.setdefault("visibility_default", "hidden")
    text = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    (dest / "problem.yaml").write_text(text, encoding="utf-8")


def _save(upload: "UploadedFile[bytes]", target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as file:
        for chunk in upload.chunks():
            file.write(chunk)


def _write_statements(data: dict[str, Any], dest: Path) -> None:
    folder = dest / "statement"
    folder.mkdir(exist_ok=True)
    for language in STATEMENT_LANGUAGES:
        markdown = folder / f"{language}.md"
        text = data.get(f"statement_{language}", "")
        if text.strip():
            markdown.write_text(text, encoding="utf-8")
        else:
            markdown.unlink(missing_ok=True)
        pdf: UploadedFile[bytes] | None = data.get(f"statement_pdf_{language}")
        if pdf is not None:
            _save(pdf, folder / f"{language}.pdf")
    if not any(folder.iterdir()):
        raise ProblemImportError(_("Write a statement in Markdown, or upload a statement PDF."))


def _write_tests(data: dict[str, Any], dest: Path) -> None:
    files: list[UploadedFile[bytes]] = data.get("tests") or []
    archive: UploadedFile[bytes] | None = data.get("tests_zip")
    if not files and archive is None:
        return  # editing: keep the tests of the version being edited
    tests = dest / "tests"
    shutil.rmtree(tests, ignore_errors=True)
    tests.mkdir()
    if archive is not None:
        _unpack_tests(archive, dest, tests)
        return
    for upload in files:
        name = Path(upload.name or "").name
        if not TEST_FILE_RE.fullmatch(name):
            raise ProblemImportError(
                _("%(name)s is not a test file: use <name>.in and <name>.out.") % {"name": name}
            )
        if (tests / name).exists():
            raise ProblemImportError(_("%(name)s is there twice.") % {"name": name})
        _save(upload, tests / name)


def _unpack_tests(archive: "UploadedFile[bytes]", dest: Path, tests: Path) -> None:
    """Take the .in/.out files from a zip, from its top level or one folder down."""
    with scratch_folder() as work:
        zipped = work / "tests.zip"
        _save(archive, zipped)
        unpacked = work / "unpacked"
        extract_zip(zipped, unpacked)  # the importer's own checks: size, count, no "..", ...
        for path in sorted(unpacked.rglob("*")):
            depth = len(path.relative_to(unpacked).parts)
            if path.is_file() and depth <= 2 and TEST_FILE_RE.fullmatch(path.name):
                if (tests / path.name).exists():
                    raise ProblemImportError(_("%(name)s is there twice.") % {"name": path.name})
                shutil.copyfile(path, tests / path.name)
    if not any(tests.iterdir()):
        raise ProblemImportError(_("The zip has no .in and .out files."))


def _write_checker(data: dict[str, Any], dest: Path) -> dict[str, str]:
    kind = data["checker"]
    folder = dest / "checker"
    if kind == "diff":
        shutil.rmtree(folder, ignore_errors=True)
        return {"type": "diff"}
    upload: UploadedFile[bytes] | None = data.get("checker_file")
    if upload is not None:
        shutil.rmtree(folder, ignore_errors=True)
        _save(upload, folder / "checker.cpp")
    if not (folder / "checker.cpp").is_file():
        raise ProblemImportError(_("A cms or testlib checker needs its checker.cpp file."))
    return {"type": kind, "file": "checker/checker.cpp"}


def _write_solutions(data: dict[str, Any], dest: Path) -> list[dict[str, Any]] | None:
    """Write the uploaded reference solutions; None if none was uploaded (keep the old ones)."""
    slots = [slot for slot in SOLUTION_SLOTS if data.get(f"solution{slot}") is not None]
    if not slots:
        return None
    folder = dest / "solutions"
    shutil.rmtree(folder, ignore_errors=True)
    solutions = []
    for slot in slots:
        upload: UploadedFile[bytes] = data[f"solution{slot}"]
        extension = Path(upload.name or "").suffix.lower()
        name = f"solution{slot}{extension}"
        _save(upload, folder / name)
        language = data[f"solution{slot}_lang"]
        solution: dict[str, Any] = {"file": f"solutions/{name}", "lang": language}
        if data.get(f"solution{slot}_score") is not None:
            solution["expected_score"] = data[f"solution{slot}_score"]
        solutions.append(solution)
    return solutions
