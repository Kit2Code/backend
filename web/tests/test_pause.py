"""The admin Grading page: pause/resume now, pause windows, and the time scale.

While grading is paused (by hand or by a pause window), the claim hands out nothing except
self-test jobs, so the site stays up and submissions wait in the queue.
"""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from accounts.models import User
from core.models import AuditLog, PauseWindow, SiteSetting
from judging.models import JudgeJob, Submission
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PAGE = "/admin/grader/grading/"
PASSWORD = "a-long-test-password-1"


@pytest.fixture
def staff() -> Client:
    client = EdgeClient()
    client.force_login(User.objects.create_superuser("boss", password=PASSWORD))
    return client


def queue_submission() -> Submission:
    user, _ = User.objects.get_or_create(username="alice")
    problem, _ = Problem.objects.get_or_create(slug="aplusb", defaults={"is_visible": True})
    version, _ = ProblemVersion.objects.get_or_create(
        problem=problem, dir_name="v001-0123abcd", defaults={"content_hash": "0" * 64}
    )
    submission = Submission.objects.create(
        user=user, problem=problem, problem_version=version, language="cpp17", source="x"
    )
    JudgeJob.objects.create(
        kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH, submission=submission
    )
    return submission


def claim() -> Any:
    client = Client(headers={"Authorization": "Bearer " + settings.WORKER_TOKEN})
    body = json.dumps({"worker_id": "w1", "free_slots": 1})
    return client.post("/internal/judge/claim", body, content_type="application/json")


def local(when: Any) -> str:
    """A time as the datetime-local field sends it (Bangkok time)."""
    return str(timezone.localtime(when).strftime("%Y-%m-%dT%H:%M"))


# ---- access ----


def test_the_page_is_for_staff_with_the_settings_permission() -> None:
    assert EdgeClient().get(PAGE).status_code == 302
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(PAGE).status_code == 403
    assert client.post(PAGE, {"action": "pause"}).status_code == 403
    assert not SiteSetting.get().grading_paused
    helper.user_permissions.add(Permission.objects.get(codename="change_sitesetting"))
    assert client.get(PAGE).status_code == 200


# ---- pause now ----


def test_pause_and_resume(staff: Client) -> None:
    queue_submission()
    assert staff.post(PAGE, {"action": "pause"}).status_code == 302
    assert SiteSetting.get().grading_paused
    assert claim().status_code == 204  # the submission waits
    assert 'data-state="paused"' in staff.get(PAGE).content.decode()
    assert staff.post(PAGE, {"action": "resume"}).status_code == 302
    assert not SiteSetting.get().grading_paused
    assert claim().json()["kind"] == "judge"
    rows = AuditLog.objects.filter(action__startswith="grading.").order_by("created_at")
    actions = list(rows.values_list("action", flat=True))
    assert actions == ["grading.pause", "grading.resume"]


def test_self_test_runs_even_while_paused(staff: Client) -> None:
    queue_submission()
    selftest = JudgeJob.objects.create(
        kind=JudgeJob.Kind.SELFTEST, priority=JudgeJob.Priority.BACKGROUND
    )
    staff.post(PAGE, {"action": "pause"})
    job = claim().json()
    assert job == {"job_id": selftest.pk, "kind": "selftest", "time_scale": 1.0}
    assert claim().status_code == 204  # but not the submission


# ---- pause windows ----


def test_a_window_from_the_past_into_the_future_pauses_until_it_ends(staff: Client) -> None:
    queue_submission()
    now = timezone.now()
    data = {
        "action": "add_window",
        "starts_at": local(now - timedelta(hours=1)),
        "ends_at": local(now + timedelta(hours=2)),
        "note": "TOI contest",
    }
    assert staff.post(PAGE, data).status_code == 302
    window = PauseWindow.objects.get()
    assert window.note == "TOI contest"
    assert claim().status_code == 204
    page = staff.get(PAGE).content.decode()
    assert "TOI contest" in page and 'data-state="paused"' in page
    # The contest is over: grading carries on by itself.
    PauseWindow.objects.update(ends_at=timezone.now() - timedelta(seconds=1))
    assert claim().json()["kind"] == "judge"
    row = AuditLog.objects.get(action="grading.window_add")
    assert row.details["note"] == "TOI contest"


def test_a_window_must_end_after_it_starts(staff: Client) -> None:
    now = timezone.now()
    data = {
        "action": "add_window",
        "starts_at": local(now + timedelta(hours=2)),
        "ends_at": local(now + timedelta(hours=1)),
        "note": "",
    }
    response = staff.post(PAGE, data)
    assert response.status_code == 200
    assert not PauseWindow.objects.exists()


def test_a_window_can_be_removed(staff: Client) -> None:
    now = timezone.now()
    window = PauseWindow.objects.create(starts_at=now, ends_at=now + timedelta(hours=1))
    response = staff.post(PAGE, {"action": "delete_window", "window": str(window.pk)})
    assert response.status_code == 302
    assert not PauseWindow.objects.exists()
    assert AuditLog.objects.get(action="grading.window_delete").details["id"] == window.pk


def test_past_windows_are_not_listed(staff: Client) -> None:
    now = timezone.now()
    PauseWindow.objects.create(
        starts_at=now - timedelta(days=3), ends_at=now - timedelta(days=2), note="long ago"
    )
    assert "long ago" not in staff.get(PAGE).content.decode()


# ---- time scale ----


def test_the_time_scale_can_be_changed(staff: Client) -> None:
    response = staff.post(PAGE, {"action": "time_scale", "time_scale": "1.5"})
    assert response.status_code == 302
    assert SiteSetting.get().time_scale == 1.5
    row = AuditLog.objects.get(action="grading.time_scale")
    assert row.details == {"old": 1.0, "new": 1.5}


@pytest.mark.parametrize("value", ["0", "-1", "0.05", "10.5", "abc", "nan", "inf"])
def test_impossible_time_scales_are_refused(staff: Client, value: str) -> None:
    response = staff.post(PAGE, {"action": "time_scale", "time_scale": value})
    assert response.status_code == 200
    assert SiteSetting.get().time_scale == 1.0
    assert not AuditLog.objects.filter(action="grading.time_scale").exists()


def test_the_model_refuses_impossible_time_scales_too() -> None:
    setting = SiteSetting.get()
    setting.time_scale = 0
    with pytest.raises(ValidationError):
        setting.full_clean()
