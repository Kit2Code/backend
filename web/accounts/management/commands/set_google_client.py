"""Set the Google sign-in client ID and secret (the secret is read from standard input).

The admin site gets a page for this later; this command is for the dev stack and for scripts.
Example: `python manage.py set_google_client --client-id ID < secret.txt`
"""

import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from core import crypto
from core.models import SiteSetting


class Command(BaseCommand):
    help = "Set the Google sign-in client ID, and the client secret read from stdin."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--client-id", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        # From stdin, not an argument: arguments show up in `ps` and in shell history.
        secret = sys.stdin.readline().strip()
        if not options["client_id"] or not secret:
            raise CommandError("Both the client ID and a secret on stdin are needed.")
        setting = SiteSetting.get()
        setting.google_client_id = options["client_id"]
        setting.google_client_secret_encrypted = crypto.encrypt_secret(secret)
        setting.save()
        self.stdout.write("Google sign-in client saved.")
