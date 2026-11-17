"""The `Submission.max_score` backfill (PLAN.md §10 #1, B-3) and storing it on complete."""

from pathlib import Path

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from pytest_django.fixtures import Settings as SettingsWrapper

BEFORE = [("judging", "0002_judgejob_result")]
AFTER = [("judging", "0003_submission_max_score")]


def migrate(targets: list[tuple[str, str]]) -> object:
    """Migrate to `targets`; return the models as of `targets` plus every other app's latest."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    others = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "judging"]
    return executor.loader.project_state(targets + others).apps


@pytest.mark.django_db(transaction=True)
def test_backfill(tmp_path: Path, settings: SettingsWrapper) -> None:
    settings.GRADER_PROBLEMS_DIR = tmp_path  # an empty store, except one old version below
    apps = migrate(BEFORE)
    User = apps.get_model("accounts", "User")  # type: ignore[attr-defined]
    Problem = apps.get_model("problems", "Problem")  # type: ignore[attr-defined]
    Version = apps.get_model("problems", "ProblemVersion")  # type: ignore[attr-defined]
    Submission = apps.get_model("judging", "Submission")  # type: ignore[attr-defined]
    user = User.objects.create(username="alice")
    problem = Problem.objects.create(slug="p", is_visible=True, max_score=100)
    old_read = Version.objects.create(
        problem=problem, dir_name="v001-aaaaaaaa", content_hash="a" * 64
    )
    old_gone = Version.objects.create(
        problem=problem, dir_name="v002-bbbbbbbb", content_hash="b" * 64
    )
    current = Version.objects.create(
        problem=problem, dir_name="v003-cccccccc", content_hash="c" * 64
    )
    Problem.objects.filter(pk=problem.pk).update(current_version=current)
    folder = tmp_path / "p" / "v001-aaaaaaaa"
    folder.mkdir(parents=True)
    source = (
        Path(__file__).resolve().parents[2] / "fixtures" / "problems" / "aplusb" / "problem.yaml"
    )
    (folder / "problem.yaml").write_text(source.read_text().replace("total: 100", "total: 200"))

    def make(version: object, score: float | None) -> int:
        row = Submission.objects.create(
            user=user,
            problem=problem,
            problem_version=version,
            language="cpp17",
            source="x",
            score=score,
        )
        return int(row.pk)

    ids = {
        "old_read": make(old_read, 10),
        "old_gone": make(old_gone, 10),
        "current": make(current, 10),
        "queued": make(current, None),
    }
    apps = migrate(AFTER)
    Submission = apps.get_model("judging", "Submission")  # type: ignore[attr-defined]
    got = {name: Submission.objects.get(pk=pk).max_score for name, pk in ids.items()}
    assert got == {"old_read": 200, "old_gone": 100, "current": 100, "queued": None}
