"""The data model: constraints that protect the data, and the indexes the hot queries rely on."""

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Max, QuerySet, Sum
from django.utils import timezone

from accounts.models import GoogleIdentity, User
from core.models import PauseWindow, RateLimitCounter, SiteSetting
from judging.models import JudgeJob, Submission, UserProblemBest
from problems.models import Problem, ProblemVersion

pytestmark = pytest.mark.django_db


def make_user(username: str = "alice") -> User:
    return User.objects.create_user(username=username)


def make_version(slug: str = "aplusb") -> ProblemVersion:
    problem = Problem.objects.create(slug=slug, title_en="A plus B")
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    problem.current_version = version
    problem.save()
    return version


def explain(queryset: QuerySet[Any, Any]) -> str:
    """The query plan Postgres would use, with sequential scans discouraged.

    With only a few rows a sequential scan is always cheapest, so the plan would never show an
    index. Turning seq scans off asks: "if the table were big, is there an index for this query?"
    """
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
    return queryset.explain()


# ---- accounts ----


@pytest.mark.parametrize("name", ["ab", "Alice", "a" * 21, "al ice", "al-ice", "อลิซ", "al.ice"])
def test_username_rule_rejects(name: str) -> None:
    user = User(username=name)
    user.set_unusable_password()
    with pytest.raises(ValidationError) as error:
        user.full_clean()
    assert "username" in error.value.message_dict


@pytest.mark.parametrize("name", ["abc", "alice_01", "a" * 20, "007"])
def test_username_rule_accepts(name: str) -> None:
    user = User(username=name)
    user.set_unusable_password()
    user.full_clean()


def test_profile_fields() -> None:
    user = User.objects.create_user(username="bob", display_name="Bob B.", school="Some School")
    user.refresh_from_db()
    assert (user.display_name, user.school) == ("Bob B.", "Some School")


def test_session_version_changes_the_session_hash() -> None:
    user = make_user()
    before = user.get_session_auth_hash()
    user.session_version += 1
    assert user.get_session_auth_hash() != before


def test_google_identity_is_unique_per_issuer_and_subject() -> None:
    GoogleIdentity.objects.create(user=make_user("alice"), issuer="https://a", subject="123")
    GoogleIdentity.objects.create(user=make_user("bob"), issuer="https://b", subject="123")
    with pytest.raises(IntegrityError), transaction.atomic():
        GoogleIdentity.objects.create(user=make_user("carol"), issuer="https://a", subject="123")


def test_a_user_has_at_most_one_google_identity() -> None:
    user = make_user()
    GoogleIdentity.objects.create(user=user, issuer="https://a", subject="1")
    with pytest.raises(IntegrityError), transaction.atomic():
        GoogleIdentity.objects.create(user=user, issuer="https://a", subject="2")


# ---- problems ----


def test_problem_slug_is_unique() -> None:
    Problem.objects.create(slug="aplusb")
    with pytest.raises(IntegrityError), transaction.atomic():
        Problem.objects.create(slug="aplusb")


def test_version_dir_is_unique_per_problem() -> None:
    version = make_version()
    with pytest.raises(IntegrityError), transaction.atomic():
        ProblemVersion.objects.create(
            problem=version.problem, dir_name=version.dir_name, content_hash="1" * 64
        )


def test_new_problems_are_hidden_and_versions_start_imported() -> None:
    version = make_version()
    assert version.problem.is_visible is False
    assert version.status == ProblemVersion.Status.IMPORTED


# ---- judging ----


def test_source_is_capped_at_64_kb_of_utf8() -> None:
    version = make_version()
    submission = Submission(
        user=make_user(), problem=version.problem, problem_version=version, language="cpp17"
    )
    submission.source = "x" * 65536
    submission.full_clean()
    # Thai letters are 3 bytes each in UTF-8: 21846 of them are 65538 bytes.
    submission.source = "ก" * 21846
    with pytest.raises(ValidationError) as error:
        submission.full_clean()
    assert "source" in error.value.message_dict


def test_compile_log_is_capped_at_64_kb() -> None:
    version = make_version()
    submission = Submission(
        user=make_user(),
        problem=version.problem,
        problem_version=version,
        language="cpp17",
        source="int main(){}",
        compile_log="e" * 65537,
    )
    with pytest.raises(ValidationError) as error:
        submission.full_clean()
    assert "compile_log" in error.value.message_dict


def test_one_best_row_per_user_and_problem() -> None:
    version = make_version()
    user = make_user()
    UserProblemBest.objects.create(
        user=user, problem=version.problem, best_score=10, reached_at=timezone.now()
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        UserProblemBest.objects.create(
            user=user, problem=version.problem, best_score=20, reached_at=timezone.now()
        )


def test_claim_query_uses_the_queue_index() -> None:
    plan = explain(
        JudgeJob.objects.filter(status=JudgeJob.Status.QUEUED)
        .order_by("-priority", "created_at")
        .select_for_update(skip_locked=True)[:2]
    )
    assert "judgejob_claim_idx" in plan


def test_expired_lease_query_uses_the_lease_index() -> None:
    plan = explain(
        JudgeJob.objects.filter(status=JudgeJob.Status.RUNNING, lease_until__lt=timezone.now())
    )
    assert "judgejob_lease_idx" in plan


def test_ranking_query_uses_the_ranking_index() -> None:
    plan = explain(
        UserProblemBest.objects.values("user")
        .annotate(total=Sum("best_score"), reached=Max("reached_at"))
        .order_by("-total", "reached")
    )
    assert "upbest_rank_idx" in plan


# ---- core ----


def test_site_setting_is_a_single_row_with_google_fields() -> None:
    first = SiteSetting.get()
    second = SiteSetting.get()
    assert first.pk == second.pk == 1
    assert first.registration_open is True
    assert first.grading_paused is False
    assert first.google_client_id == ""
    assert first.google_client_secret_encrypted == ""
    with pytest.raises(IntegrityError), transaction.atomic():
        SiteSetting.objects.create(pk=2)


def test_pause_window_must_end_after_it_starts() -> None:
    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        PauseWindow.objects.create(starts_at=now, ends_at=now - timedelta(minutes=1))


def test_rate_limit_counter_is_unique_per_key_and_window() -> None:
    window = timezone.now().replace(microsecond=0)
    RateLimitCounter.objects.create(key="login:1.2.3.4", window_start=window, count=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        RateLimitCounter.objects.create(key="login:1.2.3.4", window_start=window, count=1)
