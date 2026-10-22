"""Reading settings from the environment (config/env.py)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import env


def test_extra_admin_urls_give_hosts_and_origins() -> None:
    value = " http://100.64.1.2:8081 , http://myz.tail1234.ts.net:8081/ "
    assert env.extra_base_urls(value) == [
        ("100.64.1.2", "http://100.64.1.2:8081"),
        ("myz.tail1234.ts.net", "http://myz.tail1234.ts.net:8081"),
    ]


def test_no_extra_admin_urls() -> None:
    assert env.extra_base_urls("") == []
    assert env.extra_base_urls(" , ") == []


@pytest.mark.parametrize("bad", ["100.64.1.2:8081", "ftp://x", "http://", "*"])
def test_extra_admin_urls_must_be_full_urls(bad: str) -> None:
    with pytest.raises(ImproperlyConfigured):
        env.extra_base_urls(bad)


@pytest.mark.parametrize("bad", ["http://*", "http://*:8081", "http://.example.org"])
def test_extra_admin_urls_cannot_allow_every_host(bad: str) -> None:
    # "*" (or a leading dot) in ALLOWED_HOSTS would switch Django's host check off.
    with pytest.raises(ImproperlyConfigured):
        env.extra_base_urls(bad)
