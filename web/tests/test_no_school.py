""" "School" is no longer asked for or shown anywhere. The column stays, so old values are kept
(but never shown)."""

import pytest
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.forms import FirstSignInForm, ProfileSettingsForm
from accounts.models import User
from judging.models import UserProblemBest
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient, use_role

pytestmark = pytest.mark.django_db

SCHOOL = "Secret School Name"


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice", display_name="Alice A.", school=SCHOOL)


def scored(user: User) -> None:
    problem = Problem.objects.create(slug="p", is_visible=True, max_score=100)
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    UserProblemBest.objects.create(
        user=user, problem=problem, best_score=100, reached_at=timezone.now()
    )


def test_forms_have_no_school_field() -> None:
    assert "school" not in FirstSignInForm().fields
    assert "school" not in ProfileSettingsForm().fields


@pytest.mark.usefixtures("public_role")
def test_public_pages_never_show_school(alice: User) -> None:
    scored(alice)
    client = EdgeClient()
    client.force_login(alice)
    for path in ["/users/alice", "/ranking", "/settings", "/privacy"]:
        html = client.get(path).content.decode()
        assert SCHOOL not in html, path
        assert 'name="school"' not in html, path
    assert "School" not in EdgeClient().get("/privacy").content.decode()


@pytest.mark.usefixtures("public_role")
def test_saving_settings_keeps_the_old_value_unshown(alice: User) -> None:
    client = EdgeClient()
    client.force_login(alice)
    client.post("/settings", {"display_name": "Alicia", "school": "New School", "show_photo": "on"})
    alice.refresh_from_db()
    assert alice.display_name == "Alicia"
    assert alice.school == SCHOOL  # not changed by the form, and never shown


def test_django_admin_does_not_list_school(settings: SettingsWrapper, alice: User) -> None:
    use_role(settings, "admin")
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True, is_superuser=True))
    listing = client.get("/admin/accounts/user/").content.decode()
    change = client.get(f"/admin/accounts/user/{alice.pk}/change/").content.decode()
    assert SCHOOL not in listing
    assert 'name="school"' not in change
