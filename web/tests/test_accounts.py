"""Staff accounts on the admin role: password login, logout, log out everywhere, CSRF, limits."""

import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from tests.conftest import EdgeClient, use_role

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-test-password-1"


def make_staff(username: str = "boss") -> User:
    return User.objects.create_user(username=username, password=PASSWORD, is_staff=True)


def login_page_csrf(client: Client) -> str:
    client.get("/admin/login/")
    return client.cookies["g_admin_csrf"].value


def post_login(client: Client, username: str, password: str) -> int:
    token = login_page_csrf(client)
    response = client.post(
        "/admin/login/?next=/admin/",
        {"username": username, "password": password, "csrfmiddlewaretoken": token},
    )
    return response.status_code


def is_signed_in_to_admin(client: Client) -> bool:
    return client.get("/admin/").status_code == 200


@pytest.mark.usefixtures("admin_role")
class TestStaffLogin:
    def test_staff_can_log_in_with_a_password(self) -> None:
        make_staff()
        client = EdgeClient(enforce_csrf_checks=True)
        assert post_login(client, "boss", PASSWORD) == 302
        assert is_signed_in_to_admin(client)

    def test_wrong_password_is_refused(self) -> None:
        make_staff()
        client = EdgeClient(enforce_csrf_checks=True)
        assert post_login(client, "boss", "wrong-password-123") == 200
        assert not is_signed_in_to_admin(client)

    def test_non_staff_cannot_log_in(self) -> None:
        User.objects.create_user(username="student", password=PASSWORD)
        client = EdgeClient(enforce_csrf_checks=True)
        post_login(client, "student", PASSWORD)
        assert not is_signed_in_to_admin(client)

    def test_disabled_staff_cannot_log_in(self) -> None:
        user = make_staff()
        user.is_active = False
        user.save()
        client = EdgeClient(enforce_csrf_checks=True)
        post_login(client, "boss", PASSWORD)
        assert not is_signed_in_to_admin(client)

    def test_post_without_csrf_token_is_rejected(self) -> None:
        make_staff()
        client = EdgeClient(enforce_csrf_checks=True)
        client.get("/admin/login/")
        response = client.post("/admin/login/", {"username": "boss", "password": PASSWORD})
        assert response.status_code == 403
        assert not is_signed_in_to_admin(client)

    def test_login_is_rate_limited_per_ip_and_username(self) -> None:
        make_staff()
        client = EdgeClient(enforce_csrf_checks=True)
        for _ in range(10):
            assert post_login(client, "boss", "wrong-password-123") == 200
        # The 11th try in 15 minutes is refused, even with the right password.
        assert post_login(client, "boss", PASSWORD) == 429
        assert not is_signed_in_to_admin(client)
        # Another username from the same address is counted separately.
        make_staff("other")
        assert post_login(EdgeClient(enforce_csrf_checks=True), "other", PASSWORD) == 302

    def test_logout(self) -> None:
        client = EdgeClient()
        client.force_login(make_staff())
        assert client.post("/admin/logout/").status_code in (200, 302)
        assert not is_signed_in_to_admin(client)

    def test_log_out_everywhere(self) -> None:
        user = make_staff()
        laptop, phone = EdgeClient(), EdgeClient()
        laptop.force_login(user)
        phone.force_login(user)
        assert is_signed_in_to_admin(phone)
        assert laptop.post("/account/logout-everywhere").status_code == 302
        assert not is_signed_in_to_admin(laptop)
        assert not is_signed_in_to_admin(phone)

    def test_log_out_everywhere_needs_post_and_csrf(self) -> None:
        user = make_staff()
        client = EdgeClient(enforce_csrf_checks=True)
        client.force_login(user)
        assert client.get("/account/logout-everywhere").status_code == 405
        assert client.post("/account/logout-everywhere").status_code == 403
        assert is_signed_in_to_admin(client)

    def test_admin_role_has_no_google_sign_in(self) -> None:
        client = EdgeClient()
        for path in ["/auth/google/start", "/auth/google/callback", "/auth/welcome"]:
            assert client.get(path).status_code == 404


def test_passwords_are_hashed_with_argon2() -> None:
    assert make_staff().password.startswith("argon2")


@pytest.mark.parametrize("password", ["short1!", "12345678901", "password123"])
def test_weak_passwords_are_refused(password: str) -> None:
    with pytest.raises(ValidationError):
        validate_password(password)


def test_a_long_password_is_accepted() -> None:
    validate_password(PASSWORD)


@pytest.mark.usefixtures("public_role")
class TestPublicAccounts:
    def test_public_role_has_no_password_login_url(self) -> None:
        make_staff()
        client = EdgeClient()
        for path in ["/admin/login/", "/accounts/login/", "/login", "/auth/login"]:
            response = client.post(path, {"username": "boss", "password": PASSWORD})
            assert response.status_code == 404

    def test_logout(self, settings: SettingsWrapper) -> None:
        client = EdgeClient()
        client.force_login(User.objects.create_user(username="alice"))
        assert client.post("/auth/logout").status_code == 302
        assert "_auth_user_id" not in client.session

    def test_log_out_everywhere(self, settings: SettingsWrapper) -> None:
        user = User.objects.create_user(username="alice")
        laptop, phone = EdgeClient(), EdgeClient()
        laptop.force_login(user)
        phone.force_login(user)
        assert laptop.post("/auth/logout-everywhere").status_code == 302
        # The phone's next request finds its session no longer valid.
        use_role(settings, "public", "tests.urls_whoami")
        assert phone.get("/whoami").content == b"anonymous"

    def test_logout_needs_post(self) -> None:
        client = EdgeClient()
        client.force_login(User.objects.create_user(username="alice"))
        assert client.get("/auth/logout").status_code == 405
        assert "_auth_user_id" in client.session
