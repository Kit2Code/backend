"""The problem list and problem pages, read from the problem store, and the import command."""

import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from problems.models import Problem, ProblemVersion, Tag
from tests.conftest import EdgeClient

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]


@pytest.fixture
def store(tmp_path: Path, settings: SettingsWrapper) -> Path:
    """An empty problem store for this test."""
    folder = tmp_path / "problems"
    folder.mkdir()
    settings.GRADER_PROBLEMS_DIR = folder
    return folder


def import_fixture(source: Path, *flags: str) -> Problem:
    """Run the import command; return the problem it touched last."""
    call_command("import_problem", str(source), *flags, verbosity=0)
    return Problem.objects.order_by("-updated_at").first()  # type: ignore[return-value]


class TagCollector(HTMLParser):
    """Collect every start tag and its attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found.append((tag, attrs))


def english(client: Client) -> Client:
    client.post("/i18n/setlang/", {"language": "en", "next": "/"})
    return client


# ---- the import command ----


def test_import_registers_a_hidden_problem(store: Path) -> None:
    problem = import_fixture(FIXTURES / "problems" / "aplusb")
    assert problem.slug == "aplusb"
    assert (problem.title_th, problem.title_en) == ("เอบวกบี", "A plus B")
    assert problem.is_visible is False
    version = problem.current_version
    assert version is not None
    assert (store / "aplusb" / version.dir_name / "problem.yaml").is_file()
    assert len(version.content_hash) == 64
    assert version.report["format"] == "native"
    assert [tag.name for tag in problem.tags.all()] == ["math"]


def test_import_visible_flag(store: Path) -> None:
    assert import_fixture(FIXTURES / "problems" / "aplusb", "--visible").is_visible


def test_reimporting_the_same_content_changes_nothing(store: Path) -> None:
    first = import_fixture(FIXTURES / "problems" / "aplusb")
    second = import_fixture(FIXTURES / "problems" / "aplusb")
    assert first.pk == second.pk
    assert ProblemVersion.objects.count() == 1


def test_importing_changed_content_makes_the_new_version_current(
    store: Path, tmp_path: Path
) -> None:
    source = tmp_path / "aplusb"
    shutil.copytree(FIXTURES / "problems" / "aplusb", source)
    first = import_fixture(source).current_version
    (source / "statement" / "en.md").write_text("# A plus B\n\nNew text.\n", encoding="utf-8")
    problem = import_fixture(source)
    assert ProblemVersion.objects.count() == 2
    assert problem.current_version != first
    assert problem.current_version is not None
    assert problem.current_version.dir_name.startswith("v002-")


def test_import_errors_are_reported(store: Path, tmp_path: Path) -> None:
    from django.core.management import CommandError

    with pytest.raises(CommandError):
        call_command("import_problem", str(tmp_path / "missing"), verbosity=0)
    assert not Problem.objects.exists()


def test_thai_tags_are_fine(store: Path, tmp_path: Path) -> None:
    source = tmp_path / "aplusb"
    shutil.copytree(FIXTURES / "problems" / "aplusb", source)
    yaml = source / "problem.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8").replace("tags: [math]", "tags: [math, คณิตศาสตร์]"),
        encoding="utf-8",
    )
    import_fixture(source)
    assert set(Tag.objects.values_list("name", flat=True)) == {"math", "คณิตศาสตร์"}


# ---- the list ----


def test_list_shows_only_visible_problems(store: Path) -> None:
    import_fixture(FIXTURES / "problems" / "aplusb", "--visible")
    import_fixture(FIXTURES / "problems" / "groupmin")
    html = EdgeClient().get("/problems").content.decode()
    assert "เอบวกบี" in html
    assert 'href="/problems/aplusb"' in html
    assert "groupmin" not in html


def test_list_titles_follow_the_language(store: Path) -> None:
    import_fixture(FIXTURES / "problems" / "aplusb", "--visible")
    html = english(EdgeClient()).get("/problems").content.decode()
    assert "A plus B" in html


def test_home_lists_recent_problems(store: Path) -> None:
    import_fixture(FIXTURES / "problems" / "aplusb", "--visible")
    import_fixture(FIXTURES / "problems" / "groupmin")
    html = EdgeClient().get("/").content.decode()
    assert 'href="/problems/aplusb"' in html
    assert "/problems/groupmin" not in html


# ---- the problem page ----


def test_problem_page_shows_the_statement_limits_and_samples(store: Path) -> None:
    import_fixture(FIXTURES / "problems" / "aplusb", "--visible")
    response = EdgeClient().get("/problems/aplusb")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<h1>เอบวกบี</h1>" in html  # the Markdown statement, rendered
    assert "<em>a</em>" in html
    assert "1000" in html and "256" in html  # time and memory limits
    sample_in = (FIXTURES / "problems" / "aplusb" / "tests" / "01.in").read_text()
    assert sample_in.strip() in html
    assert "C11, C++17, C++20, Python 3" in html


def test_problem_page_in_english(store: Path) -> None:
    import_fixture(FIXTURES / "problems" / "aplusb", "--visible")
    html = english(EdgeClient()).get("/problems/aplusb").content.decode()
    assert "<h1>A plus B</h1>" in html
    assert "C11, C++17, C++20, Python 3" in html


def test_statement_falls_back_to_the_other_language(store: Path, tmp_path: Path) -> None:
    source = tmp_path / "aplusb"
    shutil.copytree(FIXTURES / "problems" / "aplusb", source)
    (source / "statement" / "th.md").unlink()
    import_fixture(source, "--visible")
    html = EdgeClient().get("/problems/aplusb").content.decode()
    assert "<h1>A plus B</h1>" in html


def test_pdf_statements_are_linked(store: Path) -> None:
    import_fixture(FIXTURES / "cms-italian" / "groupmin", "--visible")
    problem = Problem.objects.get()
    html = EdgeClient().get(f"/problems/{problem.slug}").content.decode()
    assert f'href="/problems/{problem.slug}/files/statement/th.pdf"' in html


@pytest.mark.parametrize(
    "markdown",
    [
        "<script>alert(1)</script>",
        "[click](javascript:alert(1))",
        '<img src=x onerror="alert(1)">',
        "<iframe src='https://evil.example'></iframe>",
        "[x](data:text/html,<script>alert(1)</script>)",
    ],
)
def test_statement_html_is_sanitised(store: Path, tmp_path: Path, markdown: str) -> None:
    source = tmp_path / "aplusb"
    shutil.copytree(FIXTURES / "problems" / "aplusb", source)
    (source / "statement" / "th.md").write_text(f"# Title\n\n{markdown}\n", encoding="utf-8")
    import_fixture(source, "--visible")
    html = EdgeClient().get("/problems/aplusb").content.decode()
    main = html.split("<main>")[1].split("</main>")[0]
    # Shown as text is fine; what must not exist is markup that runs or loads something.
    tags = TagCollector()
    tags.feed(main)
    assert not {"script", "iframe", "object", "embed", "style"} & {tag for tag, _ in tags.found}
    for _tag, attrs in tags.found:
        for name, value in attrs:
            assert not name.startswith("on"), (name, value)
            if name in ("href", "src"):
                assert not (value or "").strip().lower().startswith(("javascript:", "data:"))


def test_hidden_problem_is_404(store: Path) -> None:
    import_fixture(FIXTURES / "problems" / "aplusb")
    assert EdgeClient().get("/problems/aplusb").status_code == 404


def test_unknown_problem_is_404(store: Path) -> None:
    assert EdgeClient().get("/problems/nope").status_code == 404


def test_problem_without_a_version_is_404(store: Path) -> None:
    Problem.objects.create(slug="empty", is_visible=True)
    assert EdgeClient().get("/problems/empty").status_code == 404
    assert "/problems/empty" not in EdgeClient().get("/problems").content.decode()


def test_staff_also_get_404_for_hidden_problems_on_the_public_site(store: Path) -> None:
    from accounts.models import User

    import_fixture(FIXTURES / "problems" / "aplusb")
    client = EdgeClient()
    client.force_login(User.objects.create_user(username="boss", is_staff=True))
    assert client.get("/problems/aplusb").status_code == 404
