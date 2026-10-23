"""The home page: hero with the live problem count, feature cards, announcement, new problems."""

import re

import pytest
from pytest_django import DjangoAssertNumQueries
from pytest_django.fixtures import Settings as SettingsWrapper

from core.models import SiteSetting
from problems.models import Problem, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def make_problem(slug: str, visible: bool = True) -> Problem:
    problem = Problem.objects.create(slug=slug, is_visible=visible, title_th=f"โจทย์ {slug}")
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    return problem


def home_html(client: EdgeClient | None = None) -> str:
    return (client or EdgeClient()).get("/").content.decode()


def english() -> EdgeClient:
    client = EdgeClient()
    client.post("/i18n/setlang/", {"language": "en", "next": "/"})
    return client


def test_hero_has_two_lines_and_a_find_tasks_button() -> None:
    html = home_html()
    hero = re.search(r'<section class="hero">.*?</section>', html, re.S)
    assert hero, html
    assert re.search(r'<h1>\s*<span class="accent">.+?</span>\s*<span>.+?</span>\s*</h1>', hero[0])
    assert re.search(r'<a href="/problems"[^>]*role="button"[^>]*>.*?ค้นหาโจทย์', hero[0], re.S)


def test_hero_counts_only_visible_problems() -> None:
    make_problem("one")
    make_problem("two")
    make_problem("hidden", visible=False)
    Problem.objects.create(slug="no-version", is_visible=True)  # never imported fully
    assert re.search(r'<strong class="problem-count">2</strong>', home_html())


def text_of(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_english_count_reads_well() -> None:
    make_problem("one")
    assert "1 problem to try" in text_of(home_html(english()))
    make_problem("two")
    assert "2 problems to try" in text_of(home_html(english()))


def test_illustration_is_our_own_inline_svg() -> None:
    html = home_html()
    assert re.search(r'<svg class="hero-art"[^>]*aria-hidden="true"', html)
    assert "<img" not in html


def test_three_feature_cards_name_the_site(settings: SettingsWrapper) -> None:
    settings.SITE_NAME_TH = "เกรดเดอร์ทดสอบ"
    html = home_html()
    assert "ทำไมต้องเกรดเดอร์ทดสอบ" in html
    assert len(re.findall(r'<article class="feature-card">', html)) == 3


def test_announcement_is_shown_and_escaped() -> None:
    setting = SiteSetting.get()
    setting.announcement = "สอบวันเสาร์ <script>alert(1)</script>"
    setting.save()
    html = home_html()
    assert "สอบวันเสาร์" in html
    assert "<script>alert(1)</script>" not in html
    assert re.search(r'<article class="announcement">', html)


def test_no_announcement_no_box() -> None:
    assert 'class="announcement"' not in home_html()


def test_new_problems_link_to_their_pages() -> None:
    make_problem("newest")
    make_problem("hidden", visible=False)
    html = home_html()
    assert 'href="/problems/newest"' in html
    assert "โจทย์ newest" in html
    assert "/problems/hidden" not in html


def test_home_uses_few_queries(django_assert_max_num_queries: DjangoAssertNumQueries) -> None:
    for number in range(8):
        make_problem(f"p{number}")
    client = EdgeClient()
    client.get("/")  # warm the site settings
    with django_assert_max_num_queries(5):
        client.get("/")
