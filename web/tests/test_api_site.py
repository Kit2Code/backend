"""`GET /api/site` (contract §7, "Site summary")."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from core.models import PauseWindow, SiteSetting
from problems.models import Problem, ProblemVersion
from tests.conftest import use_role

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def make_problem(slug: str, *, visible: bool = True, title_en: str = "") -> Problem:
    problem = Problem.objects.create(
        slug=slug, is_visible=visible, title_th=f"โจทย์ {slug}", title_en=title_en
    )
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    return problem


def test_shape_with_no_problems(client: Client) -> None:
    response = client.get("/api/site")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.json() == {
        "announcement": "",
        "problem_count": 0,
        "registration_open": True,
        "grading_paused": False,
        "recent_problems": [],
    }


def test_settings_and_counts(client: Client) -> None:
    setting = SiteSetting.get()
    setting.announcement = "Contest on Saturday"
    setting.registration_open = False
    setting.grading_paused = True
    setting.save()
    for slug in ["a", "b"]:
        make_problem(slug)
    make_problem("hidden", visible=False)
    Problem.objects.create(slug="noversion", is_visible=True)

    body = client.get("/api/site").json()
    assert body["announcement"] == "Contest on Saturday"
    assert body["registration_open"] is False
    assert body["grading_paused"] is True
    assert body["problem_count"] == 2
    assert {p["slug"] for p in body["recent_problems"]} == {"a", "b"}


def test_recent_problems_newest_five(client: Client) -> None:
    for i in range(7):
        make_problem(f"p{i}")
    slugs = [p["slug"] for p in client.get("/api/site").json()["recent_problems"]]
    assert slugs == ["p6", "p5", "p4", "p3", "p2"]


def test_titles_follow_lang_with_fallback(client: Client) -> None:
    make_problem("aplusb", title_en="A plus B")
    make_problem("thonly")

    def titles(query: str) -> dict[str, str]:
        rows = client.get(f"/api/site{query}").json()["recent_problems"]
        return {p["slug"]: p["title"] for p in rows}

    assert titles("") == {"aplusb": "โจทย์ aplusb", "thonly": "โจทย์ thonly"}
    assert titles("?lang=en") == {"aplusb": "A plus B", "thonly": "โจทย์ thonly"}


def test_pause_window_counts_as_paused(client: Client) -> None:
    now = timezone.now()
    PauseWindow.objects.create(starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    assert client.get("/api/site").json()["grading_paused"] is True


def test_post_not_allowed(client: Client) -> None:
    assert client.post("/api/site", {}, content_type="application/json").status_code == 405


def test_not_on_admin_role(client: Client, settings: SettingsWrapper) -> None:
    use_role(settings, "admin")
    assert client.get("/api/site").status_code == 404
