"""The submit page: the form, what it creates, and the submit limits."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts.models import User
from core import ratelimit
from judging.models import JudgeJob, Submission
from problems.models import Problem
from tests.conftest import EdgeClient
from tests.test_problem_pages import FIXTURES, import_fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

URL = "/problems/aplusb/submit"
CPP = '#include <cstdio>\nint main(){int a,b;scanf("%d %d",&a,&b);printf("%d\\n",a+b);}\n'


@pytest.fixture
def problem(tmp_path: Path, settings: SettingsWrapper) -> Problem:
    """aplusb, visible, in a fresh store."""
    folder = tmp_path / "problems"
    folder.mkdir()
    settings.GRADER_PROBLEMS_DIR = folder
    return import_fixture(FIXTURES / "problems" / "aplusb", "--visible")


@pytest.fixture
def alice() -> User:
    return User.objects.create_user(username="alice")


@pytest.fixture
def signed_in(alice: User) -> Client:
    client = EdgeClient()
    client.force_login(alice)
    return client


def submit(client: Client, source: str = CPP, language: str = "cpp17", **extra: object) -> object:
    return client.post(URL, {"language": language, "source": source, **extra})


def make_old(*submissions: Submission, seconds: int = 3600) -> None:
    """Move submissions into the past and mark them judged, so no limit counts them."""
    for submission in submissions:
        Submission.objects.filter(pk=submission.pk).update(
            created_at=timezone.now() - timedelta(seconds=seconds), status=Submission.Status.DONE
        )


# ---- the page ----


def test_signed_out_visitors_are_sent_to_sign_in(problem: Problem) -> None:
    response = EdgeClient().get(URL)
    assert response.status_code == 302
    assert response["Location"] == "/auth/google/start?next=/problems/aplusb/submit"


def test_the_form_offers_the_problems_languages(problem: Problem, signed_in: Client) -> None:
    response = signed_in.get(URL)
    assert response.status_code == 200
    page = response.content.decode()
    for code in ("c11", "cpp17", "cpp20", "python3"):
        assert f'value="{code}"' in page
    assert 'enctype="multipart/form-data"' in page
    assert "csrfmiddlewaretoken" in page


def test_the_problem_page_links_to_the_submit_page(problem: Problem, signed_in: Client) -> None:
    assert f'href="{URL}"' in signed_in.get("/problems/aplusb").content.decode()


def test_hidden_problems_have_no_submit_page(problem: Problem, signed_in: Client) -> None:
    Problem.objects.filter(pk=problem.pk).update(is_visible=False)
    assert signed_in.get(URL).status_code == 404
    assert submit(signed_in).status_code == 404  # type: ignore[attr-defined]
    assert not Submission.objects.exists()


def test_admin_role_has_no_submit_page(problem: Problem, settings: SettingsWrapper) -> None:
    from tests.conftest import use_role

    use_role(settings, "admin")
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True))
    assert client.get(URL).status_code == 404


def test_only_the_problems_languages_are_offered_and_accepted(
    problem: Problem, signed_in: Client
) -> None:
    import_fixture(FIXTURES / "problems" / "python-only", "--visible")
    url = "/problems/python-only/submit"
    page = signed_in.get(url).content.decode()
    assert 'value="python3"' in page
    assert 'value="cpp17"' not in page
    assert signed_in.post(url, {"language": "cpp17", "source": CPP}).status_code == 200
    assert not Submission.objects.exists()


# ---- submitting ----


def test_submitting_queues_a_judge_job(problem: Problem, alice: User, signed_in: Client) -> None:
    response = submit(signed_in)
    submission = Submission.objects.get()
    assert response.status_code == 302  # type: ignore[attr-defined]
    assert response["Location"] == f"/submissions/{submission.pk}"  # type: ignore[index]
    assert submission.user == alice
    assert submission.problem == problem
    assert submission.problem_version == problem.current_version
    assert (submission.language, submission.source) == ("cpp17", CPP)
    assert submission.status == Submission.Status.QUEUED
    job = JudgeJob.objects.get()
    assert job.submission == submission
    assert (job.kind, job.priority, job.status) == ("judge", JudgeJob.Priority.FRESH, "queued")


def test_a_file_upload_is_used_as_the_source(problem: Problem, signed_in: Client) -> None:
    upload = SimpleUploadedFile("a.py", b"print(sum(map(int, input().split())))\n")
    response = submit(signed_in, source="", language="python3", file=upload)
    assert response.status_code == 302  # type: ignore[attr-defined]
    assert Submission.objects.get().source == "print(sum(map(int, input().split())))\n"


def test_thai_text_counts_in_utf8_bytes(problem: Problem, signed_in: Client) -> None:
    comment = "// " + "ก" * 21_845  # 3 + 65 535 bytes: just over 64 KiB
    submit(signed_in, source=comment)
    assert not Submission.objects.exists()
    submit(signed_in, source=comment[:-1])  # 65 535 bytes
    assert Submission.objects.count() == 1


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"source": ""}, "empty"),
        ({"source": "   \n"}, "only spaces"),
        ({"source": "x" * (64 * 1024 + 1)}, "too long"),
        ({"source": "int main(){}\x00"}, "NUL"),
        ({"language": "bash"}, "an unknown language"),
    ],
)
def test_bad_submissions_are_refused(
    problem: Problem, signed_in: Client, fields: dict[str, str], why: str
) -> None:
    response = signed_in.post(URL, {"language": "cpp17", "source": CPP, **fields})
    assert response.status_code == 200, why
    assert not Submission.objects.exists(), why
    assert not JudgeJob.objects.exists(), why


def test_an_upload_that_is_not_utf8_or_too_big_is_refused(
    problem: Problem, signed_in: Client
) -> None:
    for content in (b"\xff\xfe\x00binary", b"x" * (64 * 1024 + 1)):
        submit(signed_in, source="", file=SimpleUploadedFile("a.cpp", content))
    assert not Submission.objects.exists()


def test_a_huge_request_body_is_refused_unread(problem: Problem, signed_in: Client) -> None:
    upload = SimpleUploadedFile("a.cpp", b"x" * (1024 * 1024))
    assert submit(signed_in, source="", file=upload).status_code == 413  # type: ignore[attr-defined]
    assert not Submission.objects.exists()


def test_csrf_is_required(problem: Problem, alice: User) -> None:
    client = EdgeClient(enforce_csrf_checks=True)
    client.force_login(alice)
    assert submit(client).status_code == 403  # type: ignore[attr-defined]
    assert not Submission.objects.exists()


# ---- limits ----


def test_ten_second_cooldown(problem: Problem, signed_in: Client) -> None:
    assert submit(signed_in).status_code == 302  # type: ignore[attr-defined]
    assert submit(signed_in).status_code == 429  # type: ignore[attr-defined]
    first = Submission.objects.get()
    Submission.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(seconds=11))
    assert submit(signed_in).status_code == 302  # type: ignore[attr-defined]
    assert Submission.objects.count() == 2


def test_cooldown_is_per_user(problem: Problem, signed_in: Client) -> None:
    other = EdgeClient()
    other.force_login(User.objects.create_user(username="bob"))
    assert submit(signed_in).status_code == 302  # type: ignore[attr-defined]
    assert submit(other).status_code == 302  # type: ignore[attr-defined]


def test_at_most_three_waiting(
    problem: Problem, signed_in: Client, settings: SettingsWrapper
) -> None:
    settings.GRADER_SUBMIT_COOLDOWN_SECONDS = 0
    for _ in range(3):
        assert submit(signed_in).status_code == 302  # type: ignore[attr-defined]
    assert submit(signed_in).status_code == 429  # type: ignore[attr-defined]
    Submission.objects.filter(pk=Submission.objects.first().pk).update(  # type: ignore[union-attr]
        status=Submission.Status.DONE
    )
    assert submit(signed_in).status_code == 302  # type: ignore[attr-defined]
    assert Submission.objects.count() == 4


def test_the_11th_submit_in_the_hour_is_429(
    problem: Problem, signed_in: Client, settings: SettingsWrapper, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.GRADER_SUBMIT_COOLDOWN_SECONDS = 0
    settings.GRADER_SUBMIT_PER_HOUR = 10
    # One fixed moment for the limiter, so the test never straddles two hourly windows.
    moment = timezone.now()
    monkeypatch.setattr(ratelimit, "timezone", SimpleNamespace(now=lambda: moment))
    for _ in range(10):
        assert submit(signed_in).status_code == 302  # type: ignore[attr-defined]
        make_old(*Submission.objects.all())  # not waiting any more: only the hourly limit counts
    assert submit(signed_in).status_code == 429  # type: ignore[attr-defined]
    assert Submission.objects.count() == 10


def test_default_limits(settings: SettingsWrapper) -> None:
    assert settings.GRADER_SUBMIT_COOLDOWN_SECONDS == 10
    assert settings.GRADER_SUBMIT_PER_HOUR == 120
    assert settings.GRADER_SUBMIT_MAX_WAITING == 3
