"""docs/openapi.yaml is generated from the code and must be current; real answers must match it.

Regenerate with:
    uv run python web/manage.py spectacular --urlconf api.urls --file docs/openapi.yaml
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.management import call_command
from jsonschema import Draft202012Validator

from accounts.models import User
from judging.models import Submission
from tests.conftest import EdgeClient
from tests.test_archive import make_group, make_problem, solve

SCHEMA = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


def generated(tmp_path: Path) -> str:
    out = tmp_path / "openapi.yaml"
    call_command("spectacular", "--urlconf", "api.urls", "--fail-on-warn", "--file", str(out))
    return out.read_text()


def test_schema_file_is_current(tmp_path: Path) -> None:
    assert generated(tmp_path) == SCHEMA.read_text(), (
        "docs/openapi.yaml is stale; regenerate it (see this file's docstring)"
    )


def check(response: Any, component: str) -> None:
    assert response.status_code == 200, response.content
    spec = yaml.safe_load(SCHEMA.read_text())
    schema = {"$ref": f"#/components/schemas/{component}", "components": spec["components"]}
    Draft202012Validator(schema).validate(response.json())


def test_real_answers_match_the_schema() -> None:
    alice = User.objects.create_user(username="alice", display_name="Alice")
    toi = make_group("toi")
    problem = make_problem("a", toi)
    make_problem("b", make_group("sub", parent=toi))
    solve(alice, problem)
    problem.refresh_from_db()
    version = problem.current_version
    assert version is not None
    submission = Submission.objects.create(
        user=alice, problem=problem, problem_version=version, language="cpp17", source="x"
    )
    client = EdgeClient()
    client.force_login(alice)
    for anonymous in (True, False):
        c = EdgeClient() if anonymous else client
        check(c.get("/api/site"), "Site")
        check(c.get("/api/auth/me"), "Me")
        check(c.get("/api/problems"), "ProblemList")
        check(c.get("/api/groups"), "GroupList")
        check(c.get("/api/groups/toi"), "GroupDetail")
        check(c.get("/api/groups/toi/sub"), "GroupDetail")
        check(c.get("/api/submissions"), "SubmissionPage")
        check(c.get(f"/api/submissions/{submission.pk}"), "Submission")
        check(c.get("/api/ranking"), "RankingPage")
        check(c.get("/api/users/alice"), "Profile")
    check(client.get("/api/submissions/mine"), "SubmissionPage")
