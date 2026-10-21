from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        # Importing the module connects its signal receiver (the session role mark at login).
        from core import middleware  # noqa: F401
