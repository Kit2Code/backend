"""The Postgres-backed rate limiter (core.ratelimit) and the client IP it keys on."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from pytest_django.fixtures import Settings as SettingsWrapper

from core import ratelimit
from core.models import RateLimitCounter
from core.ratelimit import Limit, client_ip

pytestmark = pytest.mark.django_db

THREE_PER_MINUTE = Limit("test", max_hits=3, window=timedelta(minutes=1))
NOON = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)


def test_allows_up_to_the_limit_then_refuses() -> None:
    results = [ratelimit.hit(THREE_PER_MINUTE, "1.2.3.4", now=NOON) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_keys_are_counted_separately() -> None:
    for _ in range(3):
        ratelimit.hit(THREE_PER_MINUTE, "1.2.3.4", now=NOON)
    assert ratelimit.hit(THREE_PER_MINUTE, "5.6.7.8", now=NOON)
    assert not ratelimit.hit(THREE_PER_MINUTE, "1.2.3.4", now=NOON)


def test_limits_with_the_same_key_are_counted_separately() -> None:
    other = Limit("other", max_hits=3, window=timedelta(minutes=1))
    for _ in range(3):
        ratelimit.hit(THREE_PER_MINUTE, "1.2.3.4", now=NOON)
    assert ratelimit.hit(other, "1.2.3.4", now=NOON)


def test_a_new_window_starts_fresh() -> None:
    for _ in range(4):
        ratelimit.hit(THREE_PER_MINUTE, "k", now=NOON + timedelta(seconds=10))
    assert not ratelimit.hit(THREE_PER_MINUTE, "k", now=NOON + timedelta(seconds=59))
    assert ratelimit.hit(THREE_PER_MINUTE, "k", now=NOON + timedelta(seconds=60))


def test_long_or_odd_keys_fit_the_column() -> None:
    key = "ก" * 1000 + "\x00'; DROP TABLE x; --"
    assert ratelimit.hit(THREE_PER_MINUTE, key, now=NOON)
    stored = RateLimitCounter.objects.get()
    assert len(stored.key) <= 200
    assert "DROP" not in stored.key


def test_raw_sql_names_the_real_table() -> None:
    # The upsert is raw SQL with a fixed table name; it must match the model.
    assert RateLimitCounter._meta.db_table == ratelimit.TABLE


@pytest.mark.django_db(transaction=True)
def test_counts_are_exact_across_connections() -> None:
    # Each thread has its own database connection, like separate gunicorn processes.
    limit = Limit("race", max_hits=1000, window=timedelta(hours=1))
    allowed: list[bool] = []

    def worker() -> None:
        try:
            for _ in range(10):
                allowed.append(ratelimit.hit(limit, "same", now=NOON))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(allowed) == 80
    assert RateLimitCounter.objects.get().count == 80


def test_the_spec_limits() -> None:
    assert (ratelimit.STAFF_LOGIN.max_hits, ratelimit.STAFF_LOGIN.window) == (
        10,
        timedelta(minutes=15),
    )
    assert (ratelimit.GOOGLE_START.max_hits, ratelimit.GOOGLE_START.window) == (
        20,
        timedelta(minutes=15),
    )
    assert (ratelimit.FIRST_SIGN_IN.max_hits, ratelimit.FIRST_SIGN_IN.window) == (
        5,
        timedelta(hours=1),
    )


# ---- client IP ----


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (
            {"Cf-Connecting-Ip": "203.0.113.7", "X-Forwarded-For": "10.0.0.9"},
            "203.0.113.7",
        ),
        ({"Cf-Connecting-Ip": "2001:db8::1"}, "2001:db8::1"),
        ({"Cf-Connecting-Ip": "not an ip", "X-Forwarded-For": "10.0.0.9"}, "10.0.0.9"),
        ({"X-Forwarded-For": "10.0.0.9"}, "10.0.0.9"),
        ({}, "127.0.0.1"),
    ],
)
def test_client_ip_in_the_public_role(
    settings: SettingsWrapper, headers: dict[str, str], expected: str
) -> None:
    settings.GRADER_ROLE = "public"
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", headers=headers)
    assert client_ip(request) == expected


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        # Only Cloudflare sets Cf-Connecting-Ip, and the admin site isn't behind Cloudflare.
        ({"Cf-Connecting-Ip": "203.0.113.7", "X-Forwarded-For": "10.0.0.9"}, "10.0.0.9"),
        ({"X-Forwarded-For": "192.168.1.20"}, "192.168.1.20"),
        # The edge sets the last entry; anything before it came from the client.
        ({"X-Forwarded-For": "6.6.6.6, 192.168.1.20"}, "192.168.1.20"),
        ({"X-Forwarded-For": "garbage"}, "127.0.0.1"),
        ({}, "127.0.0.1"),
    ],
)
def test_client_ip_in_the_admin_role(
    settings: SettingsWrapper, headers: dict[str, str], expected: str
) -> None:
    settings.GRADER_ROLE = "admin"
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", headers=headers)
    assert client_ip(request) == expected
