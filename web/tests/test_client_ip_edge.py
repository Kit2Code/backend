"""Forwarded addresses are trusted only from the edge (GRADER_EDGE_ADDRESSES).

Anything else that can reach a web role directly (the worker reaches web-admin on judge_net) could
otherwise pick the address its requests are rate-limited and logged under.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from pytest_django.fixtures import Settings as SettingsWrapper

from config import env
from core.ratelimit import client_ip

EDGE = "10.89.91.10"
HEADERS = {"Cf-Connecting-Ip": "203.0.113.7", "X-Forwarded-For": "6.6.6.6, 198.51.100.4"}


@pytest.mark.parametrize("role", ["public", "admin"])
def test_headers_from_anyone_but_the_edge_are_ignored(settings: SettingsWrapper, role: str) -> None:
    settings.GRADER_ROLE = role
    settings.GRADER_EDGE_ADDRESSES = frozenset({EDGE})
    request = RequestFactory().get("/", REMOTE_ADDR="10.89.92.5", headers=HEADERS)
    assert client_ip(request) == "10.89.92.5"


@pytest.mark.parametrize(
    ("role", "expected"), [("public", "203.0.113.7"), ("admin", "198.51.100.4")]
)
def test_headers_from_the_edge_are_used(
    settings: SettingsWrapper, role: str, expected: str
) -> None:
    settings.GRADER_ROLE = role
    settings.GRADER_EDGE_ADDRESSES = frozenset({EDGE})
    request = RequestFactory().get("/", REMOTE_ADDR=EDGE, headers=HEADERS)
    assert client_ip(request) == expected


def test_no_edge_addresses_means_no_header_is_trusted(settings: SettingsWrapper) -> None:
    settings.GRADER_EDGE_ADDRESSES = frozenset()
    request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", headers=HEADERS)
    assert client_ip(request) == "127.0.0.1"


def test_edge_addresses_setting() -> None:
    assert env.ip_addresses("") == frozenset()
    assert env.ip_addresses(" 10.89.91.10 , 127.0.0.1") == {"10.89.91.10", "127.0.0.1"}
    with pytest.raises(ImproperlyConfigured, match="GRADER_EDGE_ADDRESSES"):
        env.ip_addresses("10.89.91.10, edge")
