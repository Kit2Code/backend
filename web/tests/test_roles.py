"""The two roles of the web image: GRADER_ROLE picks which URLs exist at all."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

from config.env import urlconf_for_role


def test_role_selects_its_urlconf() -> None:
    assert urlconf_for_role("public") == "config.urls_public"
    assert urlconf_for_role("admin") == "config.urls_admin"


@pytest.mark.parametrize("role", ["", "Public", "ADMIN", "internal", "admin "])
def test_unknown_role_is_refused(role: str) -> None:
    with pytest.raises(ImproperlyConfigured):
        urlconf_for_role(role)


@pytest.mark.urls("config.urls_public")
def test_public_healthz(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.urls("config.urls_admin")
def test_admin_healthz(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.urls("config.urls_public")
@pytest.mark.parametrize("path", ["/admin/", "/admin/login/", "/admin"])
def test_public_role_has_no_admin(client: Client, path: str) -> None:
    assert client.get(path).status_code == 404


@pytest.mark.urls("config.urls_admin")
def test_admin_role_serves_django_admin(client: Client) -> None:
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")
