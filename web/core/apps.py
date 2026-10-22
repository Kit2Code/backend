from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        # Importing the modules connects their signal receivers (the session role mark at login,
        # and the audit rows for staff sign-in).
        from core import audit, middleware  # noqa: F401
