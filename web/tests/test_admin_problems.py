"""The admin Problems page: every problem in one table, one-click open/close, groups."""

import re

import pytest
from django.contrib.auth.models import Permission

from accounts.models import User
from core.models import AuditLog
from problems.models import Problem, ProblemGroup, ProblemVersion
from tests.conftest import EdgeClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_role")]

PAGE = "/admin/grader/problems/"


def make_problem(
    slug: str, status: str = ProblemVersion.Status.VALID, *, visible: bool = False
) -> Problem:
    problem = Problem.objects.create(slug=slug, is_visible=visible, title_th=f"โจทย์ {slug}")
    version = ProblemVersion.objects.create(
        problem=problem, dir_name="v001-0123abcd", content_hash="0" * 64, status=status
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=version)
    problem.refresh_from_db()
    return problem


@pytest.fixture
def staff() -> EdgeClient:
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True, is_superuser=True))
    return client


def rows(html: str) -> list[str]:
    return re.findall(r'<tr id="problem-\d+" data-slug="([^"]+)"', html)


def visible(slug: str) -> bool:
    return Problem.objects.get(slug=slug).is_visible


# ---- the table ----


def test_lists_every_problem_with_status_version_and_state(staff: EdgeClient) -> None:
    make_problem("good", visible=True)
    make_problem("broken", ProblemVersion.Status.INVALID)
    Problem.objects.create(slug="empty")  # never imported: no version
    html = staff.get(PAGE).content.decode()
    assert rows(html) == ["broken", "empty", "good"]
    good = re.search(r'<tr id="problem-\d+" data-slug="good".*?</tr>', html, re.S)
    assert good and "v001-0123abcd" in good[0] and 'data-open="yes"' in good[0]


def test_search_and_filters(staff: EdgeClient) -> None:
    group = ProblemGroup.objects.create(slug="toi", name_th="TOI")
    make_problem("aplusb", visible=True)
    problem = make_problem("graph")
    problem.group = group
    problem.save()
    make_problem("broken", ProblemVersion.Status.INVALID)
    assert rows(staff.get(PAGE + "?q=graph").content.decode()) == ["graph"]
    assert rows(staff.get(PAGE + f"?group={group.pk}").content.decode()) == ["graph"]
    assert rows(staff.get(PAGE + "?group=none").content.decode()) == ["aplusb", "broken"]
    assert rows(staff.get(PAGE + "?state=open").content.decode()) == ["aplusb"]
    assert rows(staff.get(PAGE + "?state=closed").content.decode()) == ["broken", "graph"]
    assert rows(staff.get(PAGE + "?state=invalid").content.decode()) == ["broken"]


def test_page_needs_staff() -> None:
    assert EdgeClient().get(PAGE).status_code == 302


def test_dashboard_links_the_problems_page(staff: EdgeClient) -> None:
    html = staff.get("/admin/").content.decode()
    actions = re.search(r'<nav class="quick-actions".*?</nav>', html, re.S)
    assert actions and f'href="{PAGE}"' in actions[0]


# ---- open / close ----


def switch(client: EdgeClient, problem: Problem, action: str, htmx: bool = True) -> str:
    headers = {"HX-Request": "true"} if htmx else {}
    response = client.post(f"{PAGE}{problem.pk}/visibility", {"action": action}, headers=headers)
    assert response.status_code in (200, 302), response.status_code
    return response.content.decode()


def test_open_and_close_in_one_click(staff: EdgeClient) -> None:
    problem = make_problem("good")
    row = switch(staff, problem, "open")
    assert visible("good")
    assert row.lstrip().startswith(f'<tr id="problem-{problem.pk}"')  # htmx swaps the row
    assert 'data-open="yes"' in row
    switch(staff, problem, "close")
    assert not visible("good")
    actions = list(AuditLog.objects.order_by("pk").values_list("action", flat=True))
    assert actions[-2:] == ["problem.publish", "problem.hide"]


def test_refuses_to_open_a_problem_that_did_not_pass_validation(staff: EdgeClient) -> None:
    for slug, status in [
        ("broken", ProblemVersion.Status.INVALID),
        ("checking", ProblemVersion.Status.VALIDATING),
        ("new", ProblemVersion.Status.IMPORTED),
    ]:
        problem = make_problem(slug, status)
        row = switch(staff, problem, "open")
        assert not visible(slug)
        assert 'class="switch-error"' in row
    empty = Problem.objects.create(slug="empty")
    switch(staff, empty, "open")
    assert not visible("empty")
    assert not AuditLog.objects.filter(action="problem.publish").exists()


def test_without_javascript_the_switch_redirects_back(staff: EdgeClient) -> None:
    problem = make_problem("good")
    response = staff.post(f"{PAGE}{problem.pk}/visibility", {"action": "open"})
    assert response.status_code == 302 and response["Location"].startswith(PAGE)
    assert visible("good")


def test_switch_needs_post_csrf_and_permission(staff: EdgeClient) -> None:
    problem = make_problem("good")
    assert staff.get(f"{PAGE}{problem.pk}/visibility").status_code == 405
    strict = EdgeClient(enforce_csrf_checks=True)
    strict.force_login(User.objects.get(username="boss"))
    assert strict.post(f"{PAGE}{problem.pk}/visibility", {"action": "open"}).status_code == 403
    viewer = User.objects.create_user(username="viewer", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_problem"))
    client = EdgeClient()
    client.force_login(viewer)
    assert client.post(f"{PAGE}{problem.pk}/visibility", {"action": "open"}).status_code == 403
    assert not visible("good")


def test_the_admin_publish_action_uses_the_same_rule(staff: EdgeClient) -> None:
    broken = make_problem("broken", ProblemVersion.Status.INVALID)
    good = make_problem("good")
    staff.post(
        "/admin/problems/problem/",
        {"action": "publish", "_selected_action": [broken.pk, good.pk]},
    )
    assert visible("good") and not visible("broken")


# ---- groups ----


def test_add_a_group_from_the_page(staff: EdgeClient) -> None:
    toi = ProblemGroup.objects.create(slug="toi", name_th="TOI")
    response = staff.post(
        PAGE + "groups",
        {"slug": "toi20", "name_th": "TOI ครั้งที่ 20", "name_en": "TOI 20", "parent": toi.pk,
         "order": 1},
    )  # fmt: skip
    assert response.status_code == 302
    assert ProblemGroup.objects.get(slug="toi20").parent == toi
    assert AuditLog.objects.filter(action="group.add").exists()


def test_group_form_keeps_the_two_level_rule(staff: EdgeClient) -> None:
    toi = ProblemGroup.objects.create(slug="toi", name_th="TOI")
    sub = ProblemGroup.objects.create(slug="toi20", name_th="TOI20", parent=toi)
    response = staff.post(PAGE + "groups", {"slug": "deep", "name_th": "x", "parent": sub.pk})
    assert response.status_code == 200  # the page again, with the error
    assert not ProblemGroup.objects.filter(slug="deep").exists()


def test_set_a_problems_group_from_its_row(staff: EdgeClient) -> None:
    group = ProblemGroup.objects.create(slug="toi", name_th="TOI")
    problem = make_problem("good")
    row = staff.post(
        f"{PAGE}{problem.pk}/group", {"group": group.pk}, headers={"HX-Request": "true"}
    ).content.decode()
    problem.refresh_from_db()
    assert problem.group == group
    assert f'<option value="{group.pk}" selected>' in row
    staff.post(f"{PAGE}{problem.pk}/group", {"group": ""})
    problem.refresh_from_db()
    assert problem.group is None
    assert AuditLog.objects.filter(action="problem.group").count() == 2


def test_an_unknown_group_is_refused(staff: EdgeClient) -> None:
    problem = make_problem("good")
    response = staff.post(f"{PAGE}{problem.pk}/group", {"group": "999999"})
    assert response.status_code == 400
    problem.refresh_from_db()
    assert problem.group is None
