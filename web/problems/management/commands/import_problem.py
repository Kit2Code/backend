"""Import a problem (zip or folder, native or CMS Italian format) into the store and the database.

Example: `python manage.py import_problem fixtures/problems/aplusb --visible`
The admin site's import page (later) does the same, followed by validation before publishing.
"""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from grader_common.importer.errors import ProblemImportError
from grader_common.importer.pipeline import import_problem
from problems.registry import register


class Command(BaseCommand):
    help = "Import a problem zip or folder into the problem store and register it."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("source", type=Path)
        parser.add_argument(
            "--visible",
            action="store_true",
            help="Show it on the public site at once, without validation (dev and tests only).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        store_dir = Path(settings.GRADER_PROBLEMS_DIR)
        store_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = import_problem(options["source"], store_dir)
        except ProblemImportError as error:
            raise CommandError(str(error)) from None
        for warning in result.warnings:
            self.stderr.write(f"warning: {warning}")
        version = register(result, make_visible=options["visible"])
        state = "new version" if result.created else "already stored"
        self.stdout.write(f"{version} ({state})")
