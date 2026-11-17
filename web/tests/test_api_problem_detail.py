"""`GET /api/problems/{slug}` and `/files/{name}` (contract §7). The file allow-list itself is
tested in depth by test_problem_files.py; the API mounts the same view."""

from pathlib import Path

import pytest

from problems.models import Problem
from tests.conftest import EdgeClient
from tests.test_problem_files import FIXTURES, add, aplusb, body, store  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def test_detail_shape(aplusb: Problem) -> None:  # noqa: F811
    data = EdgeClient().get(f"/api/problems/{aplusb.slug}?lang=en").json()
    assert data["id"] == aplusb.pk
    assert data["slug"] == aplusb.slug
    assert data["subtasks"] == []
    assert data["statement"]["language"] in ("th", "en")
    assert data["statement"]["html"]
    assert data["samples"][0].keys() == {"name", "input", "output", "cut_short"}
    assert {"code": "cpp17", "name": "C++17"} in data["languages"]
    template = f"/api/problems/{aplusb.slug}/files/attachments/template.cpp"
    assert {"name": "attachments/template.cpp", "url": template} in data["attachments"]
    assert body(EdgeClient().get(template)) == b"int main() {}\n"


def test_subtasks_and_pdfs(store: Path) -> None:  # noqa: F811
    problem = add(FIXTURES / "cms-italian" / "groupmin", "--visible")
    data = EdgeClient().get(f"/api/problems/{problem.slug}").json()
    assert data["subtasks"] and data["subtasks"][0].keys() == {"name", "points"}
    url = data["statement"]["pdfs"]["th"]
    assert (
        url == f"/api/problems/{problem.slug}/files/statement/th.pdf?v={problem.current_version_id}"
    )
    response = EdgeClient().get(url)
    assert (response.status_code, response["Content-Type"]) == (200, "application/pdf")


def test_hidden_and_missing_are_json_404(aplusb: Problem) -> None:  # noqa: F811
    Problem.objects.filter(pk=aplusb.pk).update(is_visible=False)
    for url in [f"/api/problems/{aplusb.slug}", "/api/problems/nope"]:
        response = EdgeClient().get(url)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_files_off_the_allow_list_are_404(aplusb: Problem) -> None:  # noqa: F811
    for name in ["problem.yaml", "solutions/ok.cpp", "../../etc/passwd"]:
        assert EdgeClient().get(f"/api/problems/{aplusb.slug}/files/{name}").status_code == 404
