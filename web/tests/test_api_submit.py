"""`POST /api/problems/{slug}/submissions` (contract §9). The limits themselves live in
judging/services.py and are also covered through the old page by test_submit.py."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from judging.models import JudgeJob, Submission
from problems.models import Problem
from tests.conftest import EdgeClient
from tests.test_submit import CPP, alice, make_old, problem  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

URL = "/api/problems/aplusb/submissions"


@pytest.fixture
def client(alice: User) -> Client:  # noqa: F811
    client = EdgeClient(enforce_csrf_checks=True)
    client.force_login(alice)
    client.token = client.get("/api/auth/me").json()["csrf_token"]  # type: ignore[attr-defined]
    return client


def send(client: Client, data: Any, *, multipart: bool = False) -> Any:
    token = client.token  # type: ignore[attr-defined]
    if multipart:
        return client.post(URL, data, HTTP_X_CSRFTOKEN=token)
    return client.post(URL, data, "application/json", HTTP_X_CSRFTOKEN=token)


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def test_json_submit(problem: Problem, client: Client) -> None:  # noqa: F811
    response = send(client, {"language": "cpp17", "source": CPP})
    assert response.status_code == 201, response.content
    submission = Submission.objects.get()
    assert response.json() == {"id": submission.pk, "status": "queued"}
    assert response["Location"] == f"/api/submissions/{submission.pk}"
    assert (submission.source, submission.language) == (CPP, "cpp17")
    assert JudgeJob.objects.filter(submission=submission).exists()


def test_file_upload_wins_over_source(problem: Problem, client: Client) -> None:  # noqa: F811
    upload = SimpleUploadedFile("a.cpp", CPP.encode())
    response = send(
        client, {"language": "cpp17", "source": "ignored", "file": upload}, multipart=True
    )
    assert response.status_code == 201, response.content
    assert Submission.objects.get().source == CPP


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"language": "cobol", "source": CPP}, "language"),
        ({"language": "cpp17", "source": "   "}, "source"),
        ({"language": "cpp17", "source": "a\x00b"}, "source"),
        ({"language": "cpp17", "source": "x" * (64 * 1024 + 1)}, "source"),
    ],
)
def test_validation(problem: Problem, client: Client, data: dict[str, str], field: str) -> None:  # noqa: F811
    response = send(client, data)
    assert error(response) == (400, "validation_error")
    assert field in response.json()["error"]["fields"]
    assert not Submission.objects.exists()


def test_needs_sign_in_and_csrf(problem: Problem, alice: User) -> None:  # noqa: F811
    anonymous = EdgeClient(enforce_csrf_checks=True)
    token = anonymous.get("/api/auth/me").json()["csrf_token"]
    response = anonymous.post(
        URL, {"language": "cpp17", "source": CPP}, "application/json", HTTP_X_CSRFTOKEN=token
    )
    assert error(response) == (401, "not_authenticated")
    no_token = EdgeClient(enforce_csrf_checks=True)
    no_token.force_login(alice)
    assert error(no_token.post(URL, {}, "application/json")) == (403, "csrf_failed")


def test_hidden_problem_is_404(problem: Problem, client: Client) -> None:  # noqa: F811
    Problem.objects.filter(pk=problem.pk).update(is_visible=False)
    assert error(send(client, {"language": "cpp17", "source": CPP})) == (404, "not_found")


def test_cooldown_then_waiting_then_hourly(
    problem: Problem,  # noqa: F811
    client: Client,
    settings: SettingsWrapper,
) -> None:
    first = send(client, {"language": "cpp17", "source": CPP})
    assert first.status_code == 201
    refused = send(client, {"language": "cpp17", "source": CPP})
    assert error(refused) == (429, "cooldown")
    assert 1 <= int(refused["Retry-After"]) <= settings.GRADER_SUBMIT_COOLDOWN_SECONDS

    settings.GRADER_SUBMIT_COOLDOWN_SECONDS = 0
    settings.GRADER_SUBMIT_MAX_WAITING = 1
    refused = send(client, {"language": "cpp17", "source": CPP})
    assert error(refused) == (429, "too_many_waiting")
    assert "Retry-After" not in refused

    make_old(*Submission.objects.all())
    settings.GRADER_SUBMIT_PER_HOUR = 1  # the first submit already used the hour's one
    refused = send(client, {"language": "cpp17", "source": CPP})
    assert error(refused) == (429, "hourly_limit")
    assert 0 < int(refused["Retry-After"]) <= 3600
    assert Submission.objects.count() == 1


def test_body_over_256_kb_is_413(problem: Problem, client: Client) -> None:  # noqa: F811
    response = send(client, {"language": "cpp17", "source": "x" * (256 * 1024)})
    assert error(response) == (413, "payload_too_large")
