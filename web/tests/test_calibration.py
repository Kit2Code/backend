"""Calibration: time each problem's reference solutions on the judge CPUs and suggest a time scale.

The suggestion assumes a reference solution should use at most half of its time limit (see
docs/DECISIONS.md). The admin decides, on the Grading page.
"""

import json
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import Client

from accounts.models import User
from core.models import AuditLog
from judging.calibration import suggested_time_scale
from judging.models import JudgeJob
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PAGE = "/admin/grader/calibrate/"
PASSWORD = "a-long-test-password-1"


@pytest.fixture
def staff() -> Client:
    client = EdgeClient()
    client.force_login(User.objects.create_superuser("boss", password=PASSWORD))
    return client


def make_problem(slug: str = "groupmin") -> Problem:
    problem = Problem.objects.create(slug=slug)
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64,
        status=ProblemVersion.Status.VALID,
    )  # fmt: skip
    problem.current_version = version
    problem.save()
    return problem


def worker_post(path: str, data: dict[str, Any]) -> Any:
    client = Client(headers={"Authorization": "Bearer " + settings.WORKER_TOKEN})
    return client.post(path, json.dumps(data), content_type="application/json")


def claim() -> Any:
    return worker_post("/internal/judge/claim", {"worker_id": "w1", "free_slots": 1})


def solution(file: str, limit: float, times: list[float]) -> dict[str, Any]:
    tests = [{"name": f"t{i}", "max_time": t} for i, t in enumerate(times)]
    return {"file": file, "lang": "cpp17", "limit": limit, "tests": tests, "verdicts": ["AC"] * 5}


def calibration(*solutions: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "calibrate", "runs": 5, "solutions": list(solutions), "warnings": []}


def report(job_id: int, body: dict[str, Any]) -> Any:
    return worker_post(
        "/internal/judge/report", {"worker_id": "w1", "job_id": job_id, "report": body}
    )


# ---- the suggestion ----


@pytest.mark.parametrize(
    ("times", "limit", "expected"),
    [
        ([0.3], 1.0, 1.0),  # 2 x 0.3 = 0.6 of the limit: the contest's limits are fine
        ([0.8], 1.0, 1.6),  # needs 1.6 x the time to stay within half the limit
        ([0.805], 1.0, 1.7),  # always rounded up
        ([0.2, 1.5], 2.0, 1.5),  # the slowest test decides
        ([30.0], 1.0, 10.0),  # never more than the largest allowed scale
    ],
)
def test_suggested_scale(times: list[float], limit: float, expected: float) -> None:
    assert suggested_time_scale(calibration(solution("s.cpp", limit, times))) == expected


def test_the_slowest_solution_decides() -> None:
    body = calibration(solution("fast.cpp", 1.0, [0.1]), solution("slow.cpp", 2.0, [1.9]))
    assert suggested_time_scale(body) == 1.9


def test_no_times_no_suggestion() -> None:
    assert suggested_time_scale(calibration()) is None


# ---- the page ----


def test_the_page_is_for_staff_who_may_change_settings() -> None:
    assert EdgeClient().get(PAGE).status_code == 302
    helper = User.objects.create_user("helper", password=PASSWORD, is_staff=True)
    client = EdgeClient()
    client.force_login(helper)
    assert client.get(PAGE).status_code == 403
    helper.user_permissions.add(Permission.objects.get(codename="change_sitesetting"))
    assert client.get(PAGE).status_code == 200


def test_calibrate_queues_one_job_for_the_current_version(staff: Client) -> None:
    problem = make_problem()
    data = {"problem": str(problem.pk)}
    assert staff.post(PAGE, data).status_code == 302
    assert staff.post(PAGE, data).status_code == 302  # already waiting
    job = JudgeJob.objects.get()
    assert (job.kind, job.priority, job.problem_version) == (
        "calibrate", JudgeJob.Priority.BACKGROUND, problem.current_version,
    )  # fmt: skip
    assert AuditLog.objects.filter(action="problem.calibrate", target="groupmin").count() == 1
    assert claim().json() == {
        "job_id": job.pk, "kind": "calibrate", "problem_path": "groupmin/v001-0123abcd",
        "time_scale": 1.0,
    }  # fmt: skip


def test_calibration_results_and_suggestion_are_shown(staff: Client) -> None:
    make_problem()
    staff.post(PAGE, {"problem": str(Problem.objects.get().pk)})
    body = calibration(solution("solutions/full.cpp", 1.0, [0.25, 0.75]))
    assert report(claim().json()["job_id"], body).status_code == 204
    html = staff.get(PAGE).content.decode()
    assert "solutions/full.cpp" in html
    assert "0.75" in html  # the slowest test's time
    assert 'data-suggested="groupmin">1.5<' in html
    assert 'data-suggested-overall="1.5"' in html


def test_problems_without_a_version_are_not_listed(staff: Client) -> None:
    Problem.objects.create(slug="empty")
    assert 'data-problem="empty"' not in staff.get(PAGE).content.decode()


def test_a_calibration_report_must_be_for_a_calibration_job(staff: Client) -> None:
    make_problem()
    staff.post(PAGE, {"problem": str(Problem.objects.get().pk)})
    job_id = claim().json()["job_id"]
    validation = {"kind": "validate", "solutions": [], "warnings": []}
    assert report(job_id, validation).status_code == 409
    version = ProblemVersion.objects.get()
    assert version.status == ProblemVersion.Status.VALID  # a calibration never changes it
    assert report(job_id, calibration()).status_code == 204
    version.refresh_from_db()
    assert version.status == ProblemVersion.Status.VALID


def test_tests_cut_off_by_the_time_limit_are_left_out() -> None:
    # A brute-force reference solution meant to TLE: its times say nothing about the judge CPU.
    brute = solution("brute.cpp", 1.0, [10.0])
    brute["tests"][0]["accepted"] = False
    body = calibration(solution("full.cpp", 1.0, [0.4]), brute)
    assert suggested_time_scale(body) == 1.0
