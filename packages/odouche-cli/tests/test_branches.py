import json
from collections.abc import Callable
from typing import Self

import click
import pytest
from typer.testing import CliRunner, Result

import odouche
from odouche import Branch, Stage
from odouche_cli import _context  # pyright: ignore[reportPrivateUsage]
from odouche_cli._context import PROJECT_ENV
from odouche_cli.app import app


ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
GLOBEX = odouche.Project(id=2, name="globex", repository="globex/erp", url="https://www.odoo.sh/project/globex")

# In the order Odoo.sh might answer them: neither by stage nor by name.
BRANCHES = (
    Branch(id=4, name="feature-x", stage=Stage.DEVELOPMENT, stage_name="dev"),
    Branch(id=2, name="staging-b", stage=Stage.STAGING, stage_name="staging"),
    Branch(id=1, name="main", stage=Stage.PRODUCTION, stage_name="production"),
    Branch(id=5, name="bugfix", stage=Stage.DEVELOPMENT, stage_name="dev"),
    Branch(id=3, name="staging-a", stage=Stage.STAGING, stage_name="staging"),
)

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: answers with projects and branches, and records the calls."""

    found: tuple[Branch, ...] = BRANCHES
    error: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""
    calls: list[str]

    def __init__(self, *, read_only: bool = False) -> None:
        self.modes.append(read_only)
        if self.error is not None:
            raise self.error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def projects(self) -> list[odouche.Project]:
        self.calls.append("projects")
        return [ACME, GLOBEX]

    def branches(self, project: str) -> list[Branch]:
        self.calls.append(f"branches {project}")
        return list(self.found)


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []
        calls: list[str] = []

    monkeypatch.setattr(odouche, "Client", Client)
    return Client


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def rows(result: Result) -> list[list[str]]:
    return [line.split() for line in result.stdout.splitlines()]


def test_from_a_checkout_the_project_is_the_one_that_builds_it(client: type[StubClient], checkout: Callable[..., None]):
    checkout("feature-x", origin="git@github.com:globex/erp.git")

    result = run("branches", "list")

    assert result.exit_code == 0
    assert client.calls == ["projects", "branches globex"]
    assert rows(result) == [
        ["Name", "Stage"],
        ["main", "production"],
        ["staging-a", "staging"],
        ["staging-b", "staging"],
        ["bugfix", "development"],
        ["*", "feature-x", "development"],
    ]


def test_the_flag_names_the_project_without_listing_them(client: type[StubClient]):
    result = run("branches", "list", "--project", "acme")

    assert result.exit_code == 0
    assert client.calls == ["branches acme"]


def test_the_variable_names_the_project(client: type[StubClient], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(PROJECT_ENV, "globex")

    result = run("branches", "list")

    assert result.exit_code == 0
    assert client.calls == ["branches globex"]


def test_no_project_exits_2_and_asks_for_nothing(client: type[StubClient]):
    result = run("branches", "list")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--project" in click.unstyle(result.stderr)
    assert client.calls == []


def test_a_project_given_by_name_marks_no_branch(checkout: Callable[..., None]):
    checkout("feature-x", origin="git@github.com:globex/erp.git")

    result = run("branches", "list", "--project", "acme")

    assert rows(result)[-1] == ["feature-x", "development"]


def test_as_json_is_the_whole_model_in_the_same_order(checkout: Callable[..., None], monkeypatch: pytest.MonkeyPatch):
    checkout("feature-x", origin="git@github.com:acme/odoo.git")
    asked: list[tuple[str, ...]] = []
    git = _context._git  # pyright: ignore[reportPrivateUsage]

    def recorded(*args: str) -> str | None:
        asked.append(args)
        return git(*args)

    monkeypatch.setattr(_context, "_git", recorded)

    result = run("--format", "json", "branches", "list")

    # The resolver's one: the marker is not looked for.
    assert asked.count(("branch", "--show-current")) == 1

    assert json.loads(result.stdout) == [
        {"id": 1, "name": "main", "stage": "production", "stage_name": "production"},
        {"id": 3, "name": "staging-a", "stage": "staging", "stage_name": "staging"},
        {"id": 2, "name": "staging-b", "stage": "staging", "stage_name": "staging"},
        {"id": 5, "name": "bugfix", "stage": "development", "stage_name": "dev"},
        {"id": 4, "name": "feature-x", "stage": "development", "stage_name": "dev"},
    ]


def test_a_stage_filter_lists_only_that_stage():
    result = run("branches", "list", "--project", "acme", "--stage", "staging")

    assert rows(result)[1:] == [["staging-a", "staging"], ["staging-b", "staging"]]


def test_a_stage_filter_given_twice_lists_both():
    result = run("branches", "list", "--project", "acme", "--stage", "development", "--stage", "production")

    assert [row[0] for row in rows(result)[1:]] == ["main", "bugfix", "feature-x"]


def test_an_invalid_stage_is_a_usage_error_and_not_an_empty_list(client: type[StubClient]):
    result = run("branches", "list", "--project", "acme", "--stage", "stagin")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "stagin" in click.unstyle(result.stderr)
    assert client.calls == []


def test_an_unknown_stage_shows_what_odoo_sh_calls_it_and_sorts_last(client: type[StubClient]):
    client.found = (Branch(id=9, name="archive", stage=Stage.UNKNOWN, stage_name="archived"), *BRANCHES)

    table = run("branches", "list", "--project", "acme")
    as_json = run("--format", "json", "branches", "list", "--project", "acme")
    filtered = run("branches", "list", "--project", "acme", "--stage", "unknown")

    assert rows(table)[-1] == ["archive", "archived"]
    assert json.loads(as_json.stdout)[-1] == {"id": 9, "name": "archive", "stage": "unknown", "stage_name": "archived"}
    assert rows(filtered)[1:] == [["archive", "archived"]]


def test_the_listing_is_asked_once_whatever_the_number_of_branches(client: type[StubClient]):
    client.found = tuple(Branch(id=n, name=f"dev-{n}", stage=Stage.DEVELOPMENT, stage_name="dev") for n in range(10))

    result = run("branches", "list", "--project", "acme")

    assert len(rows(result)) == 11
    assert client.calls == ["branches acme"]


def test_no_branches_is_a_line_on_stderr(client: type[StubClient]):
    client.found = ()

    result = run("branches", "list", "--project", "acme")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == "No branches.\n"


def test_a_filter_that_matches_nothing_as_json_is_an_empty_list(client: type[StubClient]):
    client.found = BRANCHES[:1]

    result = run("--format", "json", "branches", "list", "--project", "acme", "--stage", "production")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_logged_out_exits_3_and_names_the_login(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("branches", "list", "--project", "acme")

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "osh auth login" in result.stderr


@pytest.mark.parametrize("command", [("branches", "--help"), ("branches", "list", "--help")])
def test_help_opens_neither_the_keyring_nor_the_network(client: type[StubClient], command: tuple[str, ...]):
    client.error = odouche.KeyringUnavailableError()

    result = run(*command)

    assert result.exit_code == 0
    assert "Example: osh branches list" in click.unstyle(result.output)
