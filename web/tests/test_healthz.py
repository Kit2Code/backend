"""/healthz answers whatever the Host header: Docker's healthcheck calls 127.0.0.1, while production
only accepts the site's own host name."""

import pytest
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from tests.conftest import use_role


@pytest.mark.parametrize("role", ["public", "admin"])
def test_healthz_answers_on_any_host(settings: SettingsWrapper, role: str) -> None:
    use_role(settings, role)
    settings.ALLOWED_HOSTS = ["grader.example.org"]
    response = Client().get("/healthz", HTTP_HOST="127.0.0.1:8000")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_other_paths_still_check_the_host(settings: SettingsWrapper) -> None:
    use_role(settings, "public")
    settings.ALLOWED_HOSTS = ["grader.example.org"]
    response = Client().get("/", HTTP_HOST="127.0.0.1:8000", HTTP_X_EDGE_ZONE="public")
    assert response.status_code == 400
