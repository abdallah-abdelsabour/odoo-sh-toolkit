import json
from typing import Self

import click
import pytest
from typer.testing import CliRunner, Result

import odouche
from odouche_cli.app import app


ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
GLOBEX = odouche.Project(id=2, name="globex", repository="globex/erp", url="https://www.odoo.sh/project/globex")

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: answers with projects or raises."""

    found: tuple[odouche.Project, ...] = (ACME, GLOBEX)
    error: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""

    def __init__(self, *, read_only: bool = False) -> None:
        self.modes.append(read_only)
        if self.error is not None:
            raise self.error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def projects(self) -> list[odouche.Project]:
        return list(self.found)


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []

    monkeypatch.setattr(odouche, "Client", Client)
    return Client


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def test_list_shows_the_name_the_repository_and_the_address():
    result = run("projects", "list")

    assert result.exit_code == 0
    assert [line.split() for line in result.stdout.splitlines()] == [
        ["Name", "Repository", "URL"],
        ["acme", "acme/odoo", "https://www.odoo.sh/project/acme"],
        ["globex", "globex/erp", "https://www.odoo.sh/project/globex"],
    ]


def test_list_as_json_is_the_whole_model():
    result = run("--format", "json", "projects", "list")

    assert json.loads(result.stdout) == [
        {"id": 1, "name": "acme", "repository": "acme/odoo", "url": "https://www.odoo.sh/project/acme"},
        {"id": 2, "name": "globex", "repository": "globex/erp", "url": "https://www.odoo.sh/project/globex"},
    ]


def test_no_projects_is_a_line_on_stderr(client: type[StubClient]):
    client.found = ()

    result = run("projects", "list")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == "No projects.\n"


def test_no_projects_as_json_is_an_empty_list(client: type[StubClient]):
    client.found = ()

    result = run("--format", "json", "projects", "list")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_logged_out_exits_3_and_names_the_login(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("projects", "list")

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "osh auth login" in result.stderr


@pytest.mark.parametrize("command", [("projects", "--help"), ("projects", "list", "--help")])
def test_help_opens_neither_the_keyring_nor_the_network(client: type[StubClient], command: tuple[str, ...]):
    client.error = odouche.KeyringUnavailableError()

    result = run(*command)

    assert result.exit_code == 0
    assert "Example: osh projects list" in click.unstyle(result.output)
