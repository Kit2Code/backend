"""Print (and save) the first-run setup link while no superuser exists. Run at web-admin start."""

from typing import Any

from django.core.management.base import BaseCommand

from core import setup


class Command(BaseCommand):
    help = "Make a one-time setup link for the first superuser, if there is none yet."

    def handle(self, *args: Any, **options: Any) -> None:
        link = setup.issue_link()
        if link is None:
            return
        self.stdout.write(
            "\n"
            "  No admin account exists yet. Create one with this link (valid 24 hours, once):\n"
            f"  {link}\n"
        )
        try:
            setup.write_link_file(link)
        except OSError as error:
            # The link above still works; only the copy in the uploads folder is missing.
            self.stderr.write(f"setup: could not write {setup.link_file()}: {error}")
            return
        self.stdout.write(f"  (also saved in {setup.link_file()})\n")
