"""The audit log: every admin action, Django admin edits included, leaves a row.

Each row says who, what, to what, when and from which IP address.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client
from django.utils import timezone

from accounts.models import User
from config.admin import AuditedModelAdmin
from core.models import AuditLog, PauseWindow
from judging.internal import refresh_best
from judging.models import JudgeJob, Submission, UserProblemBest
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PASSWORD = "a-long-test-password-1"


@pytest.fixture
def boss() -> User:
    return User.objects.create_superuser("boss", password=PASSWORD)


@pytest.fixture
def staff(boss: User) -> Client:
    client = EdgeClient()
    client.force_login(boss)
    AuditLog.objects.all().delete()  # leave out the sign-in itself
    return client


def make_problem(slug: str = "aplusb", status: str = ProblemVersion.Status.VALID) -> Problem:
    problem = Problem.objects.create(slug=slug)
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64, status=status
    )
    problem.current_version = version
    problem.save()
    return problem


def make_submission(user: User, problem: Problem, **fields: Any) -> Submission:
    assert problem.current_version is not None
    fields.setdefault("source", "int main(){}")
    return Submission.objects.create(
        user=user, problem=problem, problem_version=problem.current_version, language="cpp17",
        **fields,
    )  # fmt: skip


def run_action(client: Client, model: str, action: str, *pks: int) -> Any:
    data = {"action": action, "_selected_action": [str(pk) for pk in pks]}
    return client.post(f"/admin/{model}/", data)


def only_row(action: str) -> AuditLog:
    rows = list(AuditLog.objects.filter(action=action))
    assert len(rows) == 1, [(r.action, r.target) for r in AuditLog.objects.all()]
    return rows[0]


# ---- every registered model is audited ----


def test_every_admin_page_writes_the_audit_log() -> None:
    for model, model_admin in admin.site._registry.items():
        assert isinstance(model_admin, AuditedModelAdmin), model


# ---- Django admin edits ----


def test_adding_through_the_admin_is_audited(staff: Client, boss: User) -> None:
    starts = timezone.localtime() + timedelta(days=1)
    data = {
        "starts_at_0": starts.strftime("%Y-%m-%d"),
        "starts_at_1": "09:00:00",
        "ends_at_0": starts.strftime("%Y-%m-%d"),
        "ends_at_1": "17:00:00",
        "note": "contest day",
    }
    assert staff.post("/admin/core/pausewindow/add/", data).status_code == 302
    window = PauseWindow.objects.get()
    row = only_row("admin.add")
    assert row.actor == boss and row.actor_username == "boss"
    assert row.ip == "127.0.0.1"
    assert row.target == str(window)
    assert row.details["model"] == "core.pausewindow"
    assert row.details["id"] == window.pk
    assert row.created_at is not None


def test_changing_through_the_admin_is_audited(staff: Client) -> None:
    now = timezone.now()
    window = PauseWindow.objects.create(starts_at=now, ends_at=now + timedelta(hours=1))
    local = timezone.localtime(window.starts_at)
    end = timezone.localtime(window.ends_at)
    data = {
        "starts_at_0": local.strftime("%Y-%m-%d"),
        "starts_at_1": local.strftime("%H:%M:%S"),
        "ends_at_0": end.strftime("%Y-%m-%d"),
        "ends_at_1": end.strftime("%H:%M:%S"),
        "note": "changed",
    }
    assert staff.post(f"/admin/core/pausewindow/{window.pk}/change/", data).status_code == 302
    row = only_row("admin.change")
    assert row.details["id"] == window.pk
    assert "Note" in str(row.details["message"])


def test_deleting_through_the_admin_is_audited(staff: Client) -> None:
    now = timezone.now()
    one = PauseWindow.objects.create(starts_at=now, ends_at=now + timedelta(hours=1))
    two = PauseWindow.objects.create(starts_at=now, ends_at=now + timedelta(hours=2))
    three = PauseWindow.objects.create(starts_at=now, ends_at=now + timedelta(hours=3))
    response = staff.post(f"/admin/core/pausewindow/{one.pk}/delete/", {"post": "yes"})
    assert response.status_code == 302
    response = staff.post(
        "/admin/core/pausewindow/",
        {"action": "delete_selected", "_selected_action": [two.pk, three.pk], "post": "yes"},
    )
    assert response.status_code == 302
    assert not PauseWindow.objects.exists()
    rows = AuditLog.objects.filter(action="admin.delete")
    assert sorted(row.details["id"] for row in rows) == sorted([one.pk, two.pk, three.pk])


# ---- the audit log itself ----


def test_the_audit_log_can_be_read_but_not_changed(staff: Client) -> None:
    row = AuditLog.objects.create(action="x.y", actor_username="someone")
    assert staff.get("/admin/core/auditlog/").status_code == 200
    assert staff.get(f"/admin/core/auditlog/{row.pk}/change/").status_code == 200  # view only
    assert staff.post(f"/admin/core/auditlog/{row.pk}/change/", {}).status_code == 403
    assert staff.get("/admin/core/auditlog/add/").status_code == 403
    assert staff.post(f"/admin/core/auditlog/{row.pk}/delete/", {"post": "yes"}).status_code == 403
    assert AuditLog.objects.filter(pk=row.pk).exists()


# ---- sign-in ----


def test_staff_sign_in_and_failures_are_audited(boss: User) -> None:
    client = EdgeClient()
    client.post("/admin/login/", {"username": "boss", "password": "wrong-password"})
    failed = only_row("auth.login_failed")
    assert failed.details == {"username": "boss"}
    assert failed.ip == "127.0.0.1"
    assert "wrong-password" not in str(failed.details)
    client.post("/admin/login/", {"username": "boss", "password": PASSWORD})
    assert only_row("auth.login").actor == boss
    client.post("/admin/logout/")
    assert only_row("auth.logout").actor == boss


# ---- actions ----


def test_publish_and_hide_are_audited(staff: Client) -> None:
    problem = make_problem()
    run_action(staff, "problems/problem", "publish", problem.pk)
    problem.refresh_from_db()
    assert problem.is_visible
    assert only_row("problem.publish").target == "aplusb"
    run_action(staff, "problems/problem", "hide", problem.pk)
    problem.refresh_from_db()
    assert not problem.is_visible
    assert only_row("problem.hide").target == "aplusb"


def test_a_problem_whose_version_is_not_valid_is_not_published(staff: Client) -> None:
    problem = make_problem(status=ProblemVersion.Status.INVALID)
    run_action(staff, "problems/problem", "publish", problem.pk)
    problem.refresh_from_db()
    assert not problem.is_visible
    assert not AuditLog.objects.filter(action="problem.publish").exists()


def test_rejudge_queues_the_submission_again_and_is_audited(staff: Client, boss: User) -> None:
    problem = make_problem()
    done = make_submission(boss, problem, status=Submission.Status.DONE, verdict="AC", score=100)
    waiting = make_submission(boss, problem)
    JudgeJob.objects.create(
        kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH, submission=waiting
    )
    run_action(staff, "judging/submission", "rejudge", done.pk, waiting.pk)
    done.refresh_from_db()
    assert done.status == Submission.Status.QUEUED
    job = JudgeJob.objects.get(submission=done)
    assert (job.kind, job.priority, job.status) == ("judge", JudgeJob.Priority.REJUDGE, "queued")
    # Already waiting for the judge: no second job.
    assert JudgeJob.objects.filter(submission=waiting).count() == 1
    assert only_row("submission.rejudge").details == {"ids": [done.pk]}


def test_disable_and_enable_users_are_audited(staff: Client) -> None:
    alice = User.objects.create_user("alice")
    run_action(staff, "accounts/user", "disable", alice.pk)
    alice.refresh_from_db()
    assert not alice.is_active
    assert only_row("user.disable").target == "alice"
    run_action(staff, "accounts/user", "enable", alice.pk)
    alice.refresh_from_db()
    assert alice.is_active
    assert only_row("user.enable").target == "alice"


def test_actions_need_the_change_permission(boss: User) -> None:
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    helper.user_permissions.add(Permission.objects.get(codename="view_problem"))
    problem = make_problem()
    client = EdgeClient()
    client.force_login(helper)
    run_action(client, "problems/problem", "publish", problem.pk)
    problem.refresh_from_db()
    assert not problem.is_visible


# ---- views ----


def test_submission_admin_shows_the_code_read_only(staff: Client, boss: User) -> None:
    submission = make_submission(boss, make_problem(), source="int main(){ /* <b>x</b> */ }")
    page = staff.get(f"/admin/judging/submission/{submission.pk}/change/")
    assert page.status_code == 200
    assert "/* &lt;b&gt;x&lt;/b&gt; */" in page.content.decode()
    assert 'name="source"' not in page.content.decode()


def test_users_list_shows_who_is_google_linked(staff: Client) -> None:
    page = staff.get("/admin/accounts/user/")
    assert page.status_code == 200
    assert "Google" in page.content.decode()


# ---- a rejudge may lower the best score ----


def test_a_rejudged_result_replaces_the_best_score(staff: Client, boss: User) -> None:
    problem = make_problem()
    submission = make_submission(
        boss, problem, status=Submission.Status.DONE, verdict="AC", score=100
    )
    UserProblemBest.objects.create(
        user=boss, problem=problem, best_score=100, reached_at=submission.created_at,
        submission=submission,
    )  # fmt: skip
    Submission.objects.filter(pk=submission.pk).update(score=40)
    refresh_best(boss.pk, problem.pk)
    best = UserProblemBest.objects.get()
    assert best.best_score == 40
