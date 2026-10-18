import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Self

import click
import pytest
from typer.testing import CliRunner, Result

import odouche
from odouche import Branch, Build, BuildResult, BuildStatus, Commit, Stage
from odouche_cli import _time
from odouche_cli._context import BRANCH_ENV
from odouche_cli.app import app


NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
MAIN = Branch(id=1, name="main", stage=Stage.PRODUCTION, stage_name="production")
FEATURE = Branch(id=4, name="feature-x", stage=Stage.DEVELOPMENT, stage_name="dev")

ESCAPE = "\x1b[2J\x1b]0;owned\x07"


def build(number: int, *, age: timedelta, message: str, **changes: object) -> Build:
    commit = Commit(
        hash=f"{number:x}".rjust(40, "a"),
        message=message,
        author="Ann Example",
        timestamp=NOW - age - timedelta(hours=1),
        url=f"https://github.com/acme/odoo/commit/{number:x}".ljust(40, "a"),
    )
    made = Build(
        id=number,
        name=f"acme-feature-x-{number}",
        branch_id=FEATURE.id,
        branch_name=FEATURE.name,
        commit=commit,
        status=BuildStatus.DONE,
        status_name="done",
        result=BuildResult.SUCCESS,
        result_name="success",
        status_info=None,
        started_at=NOW - age,
        url=f"https://acme-feature-x-{number}.dev.odoo.com",
    )
    return replace(made, **changes)  # pyright: ignore[reportArgumentType]


RUNNING = build(
    104,
    age=timedelta(seconds=20),
    message="Add the report\n\nWith its tests.",
    status=BuildStatus.PROGRESS,
    status_name="progress",
    result=None,
    result_name=None,
    status_info="Installing: account",
)
FAILED = build(103, age=timedelta(minutes=3), message="Fix the tax", result=BuildResult.FAILED, result_name="failed")
WARNED = build(102, age=timedelta(hours=5), message="Bump", result=BuildResult.WARNING, result_name="warning")
PASSED = build(101, age=timedelta(days=2), message="Start", status=BuildStatus.DROPPED, status_name="dropped")
BUILDS = (RUNNING, FAILED, WARNED, PASSED)

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: answers with branches and builds, and records the calls."""

    found: tuple[Build, ...] = BUILDS
    error: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""
    refused: odouche.OdoucheError | None = None
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
        return [ACME]

    def branches(self, project: str) -> list[Branch]:
        self.calls.append(f"branches {project}")
        return [MAIN, FEATURE]

    def builds(self, branch: Branch, *, limit: int = 4) -> list[Build]:
        self.calls.append(f"builds {branch.name} {limit}")
        if self.refused is not None:
            raise self.refused
        return list(self.found[:limit])

    def latest_build(self, branch: Branch) -> Build | None:
        self.calls.append(f"latest {branch.name}")
        return self.found[0] if self.found else None


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []
        calls: list[str] = []

    monkeypatch.setattr(odouche, "Client", Client)
    monkeypatch.setattr(_time, "_now", lambda: NOW)
    return Client


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def rows(result: Result) -> list[list[str]]:
    """Split each line into its cells, which two spaces or more separate."""
    return [re.split(r"\s{2,}", line) for line in result.stdout.splitlines()]


def pairs(result: Result) -> dict[str, str]:
    return {row[0]: row[1] if len(row) > 1 else "" for row in rows(result)}


def test_the_list_has_each_build_with_its_subject_and_its_age():
    result = run("builds", "list", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 0
    assert rows(result) == [
        ["ID", "Status", "Result", "Commit", "Subject", "Age"],
        ["104", "progress", "aaaaaaa", "Add the report", "less than a minute ago"],
        ["103", "done", "failed", "aaaaaaa", "Fix the tax", "3 minutes ago"],
        ["102", "done", "warning", "aaaaaaa", "Bump", "5 hours ago"],
        ["101", "dropped", "success", "aaaaaaa", "Start", "2 days ago"],
    ]


def test_the_project_and_the_branch_come_from_the_checkout(client: type[StubClient], checkout: Callable[..., None]):
    checkout("feature-x", origin="git@github.com:acme/odoo.git")

    result = run("builds", "list")

    assert result.exit_code == 0
    assert client.calls == ["projects", "branches acme", "builds feature-x 4"]


def test_the_variable_names_the_branch(client: type[StubClient], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(BRANCH_ENV, "main")

    result = run("builds", "list", "--project", "acme")

    assert result.exit_code == 0
    assert client.calls == ["branches acme", "builds main 4"]


def test_a_limit_lists_that_many(client: type[StubClient]):
    result = run("builds", "list", "--project", "acme", "--branch", "feature-x", "--limit", "2")

    assert [row[0] for row in rows(result)[1:]] == ["104", "103"]
    assert client.calls[-1] == "builds feature-x 2"


def test_a_limit_under_one_is_a_usage_error(client: type[StubClient]):
    result = run("builds", "list", "--project", "acme", "--branch", "feature-x", "--limit", "0")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert client.calls == []


def test_the_list_as_json_is_the_whole_model_with_iso_times():
    result = run("--format", "json", "builds", "list", "--project", "acme", "--branch", "feature-x")

    listed = json.loads(result.stdout)
    assert [found["id"] for found in listed] == [104, 103, 102, 101]
    assert listed[1] == {
        "id": 103,
        "name": "acme-feature-x-103",
        "branch_id": 4,
        "branch_name": "feature-x",
        "commit": {
            "hash": FAILED.commit.hash,
            "message": "Fix the tax",
            "author": "Ann Example",
            "timestamp": "2026-03-10T10:57:00+00:00",
            "url": FAILED.commit.url,
        },
        "status": "done",
        "status_name": "done",
        "result": "failed",
        "result_name": "failed",
        "status_info": None,
        "started_at": "2026-03-10T11:57:00+00:00",
        "url": "https://acme-feature-x-103.dev.odoo.com",
    }


def test_no_builds_is_a_line_on_stderr(client: type[StubClient]):
    client.found = ()

    result = run("builds", "list", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == "No builds.\n"


def test_no_branch_exits_2_and_asks_for_nothing(client: type[StubClient]):
    result = run("builds", "list", "--project", "acme")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--branch" in click.unstyle(result.stderr)
    assert client.calls == []


@pytest.mark.parametrize("command", ["list", "show"])
def test_a_branch_the_project_does_not_have_exits_4(client: type[StubClient], command: str):
    result = run("builds", command, "--project", "acme", "--branch", "feature-y")

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "Project acme has no branch feature-y.\n"
    assert client.calls == ["branches acme"]


def test_an_unknown_status_and_result_show_what_odoo_sh_calls_them(client: type[StubClient]):
    client.found = (
        replace(
            FAILED,
            status=BuildStatus.UNKNOWN,
            status_name="archived",
            result=BuildResult.UNKNOWN,
            result_name="flaky",
        ),
    )

    result = run("builds", "list", "--project", "acme", "--branch", "feature-x")

    assert rows(result)[1][:3] == ["103", "archived", "flaky"]


def test_a_build_started_ahead_of_the_clock_is_not_said_to_be_past(client: type[StubClient]):
    client.found = (replace(RUNNING, started_at=NOW + timedelta(days=3)),)

    result = run("builds", "list", "--project", "acme", "--branch", "feature-x")

    assert rows(result)[1][-1] == "in 3 days"


def test_a_build_waiting_for_a_worker_has_no_age(client: type[StubClient]):
    client.found = (replace(RUNNING, started_at=None),)

    result = run("builds", "list", "--project", "acme", "--branch", "feature-x")

    assert rows(result)[1] == ["104", "progress", "aaaaaaa", "Add the report"]


def test_show_defaults_to_the_latest_build_of_the_checkout_branch(
    client: type[StubClient], checkout: Callable[..., None]
):
    checkout("feature-x", origin="git@github.com:acme/odoo.git")

    result = run("builds", "show")

    assert result.exit_code == 0
    assert client.calls == ["projects", "branches acme", "latest feature-x"]
    assert pairs(result) == {
        "ID": "104",
        "Name": "acme-feature-x-104",
        "Branch": "feature-x",
        "Status": "progress",
        "Result": "",
        "Info": "Installing: account",
        "Started": "less than a minute ago",
        "Commit": RUNNING.commit.hash,
        "Subject": "Add the report",
        "Author": "Ann Example",
        "Committed": "1 hour ago",
        "URL": "https://acme-feature-x-104.dev.odoo.com",
    }


def test_show_with_an_identifier_shows_that_build(client: type[StubClient]):
    result = run("builds", "show", "102", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 0
    assert client.calls == ["branches acme", "builds feature-x 4"]
    assert pairs(result)["ID"] == "102"
    assert pairs(result)["Result"] == "warning"


def test_show_a_failed_latest_build_exits_0(client: type[StubClient]):
    client.found = (FAILED, WARNED)

    table = run("builds", "show", "--project", "acme", "--branch", "feature-x")
    as_json = run("--format", "json", "builds", "show", "--project", "acme", "--branch", "feature-x")

    assert table.exit_code == 0
    assert pairs(table)["Result"] == "failed"
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout)["result"] == "failed"


def test_show_as_json_is_the_whole_model():
    result = run("--format", "json", "builds", "show", "101", "--project", "acme", "--branch", "feature-x")

    shown = json.loads(result.stdout)
    assert shown["id"] == 101
    assert shown["url"] == "https://acme-feature-x-101.dev.odoo.com"
    assert shown["commit"]["author"] == "Ann Example"
    assert shown["started_at"] == "2026-03-08T12:00:00+00:00"


def test_show_a_build_that_is_not_among_the_latest_exits_4():
    result = run("builds", "show", "7", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "Build 7 is not among the latest builds of branch feature-x of acme.\n"


def test_show_keeps_the_message_of_a_branch_out_of_reach(client: type[StubClient]):
    client.refused = odouche.NotFoundError("The session's user can reach no branch numbered 4.")

    result = run("builds", "show", "7", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "The session's user can reach no branch numbered 4.\n"


def test_show_on_a_branch_without_a_build_exits_4_and_says_so(client: type[StubClient]):
    client.found = ()

    result = run("builds", "show", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "Branch feature-x of acme has no build.\n"


def test_an_identifier_that_is_not_a_number_is_a_usage_error(client: type[StubClient]):
    result = run("builds", "show", "latest", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 2
    assert client.calls == []


@pytest.mark.parametrize("command", [("list",), ("show",)])
def test_an_escape_sequence_in_a_commit_is_not_emitted_by_the_table(client: type[StubClient], command: tuple[str, ...]):
    commit = replace(FAILED.commit, message=f"Fix{ESCAPE} the tax\x9b31m", author=f"Ann{ESCAPE}")
    client.found = (replace(FAILED, commit=commit),)

    result = run("builds", *command, "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 0
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", result.stdout)
    assert "Fix[2J]0;owned the tax31m" in result.stdout


def test_an_escape_sequence_in_a_commit_is_escaped_as_json(client: type[StubClient]):
    commit = replace(FAILED.commit, message=f"Fix{ESCAPE}\x9b31m")
    client.found = (replace(FAILED, commit=commit),)

    result = run("--format", "json", "builds", "show", "--project", "acme", "--branch", "feature-x")

    assert "\x1b" not in result.stdout
    assert "\x9b" not in result.stdout
    assert json.loads(result.stdout)["commit"]["message"] == commit.message


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("command", [("list",), ("show", "103")])
def test_on_a_terminal_the_result_is_coloured_and_keeps_its_label(command: tuple[str, ...]):
    result = run("builds", *command, "--project", "acme", "--branch", "feature-x")

    assert re.search(r"\x1b\[31mfailed *\x1b\[0m", result.stdout)


@pytest.mark.parametrize("command", [("list",), ("show", "103")])
def test_off_a_terminal_the_result_is_its_label_alone(command: tuple[str, ...]):
    result = run("builds", *command, "--project", "acme", "--branch", "feature-x")

    assert "failed" in result.stdout
    assert "\x1b" not in result.stdout


def test_logged_out_exits_3_and_names_the_login(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("builds", "show", "--project", "acme", "--branch", "feature-x")

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "osh auth login" in result.stderr


@pytest.mark.parametrize("command", ["list", "show"])
@pytest.mark.parametrize("branch", [(), ("--branch", "")])
def test_no_branch_while_logged_out_is_still_a_usage_error(
    client: type[StubClient], command: str, branch: tuple[str, ...]
):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("builds", command, "--project", "acme", *branch)

    assert result.exit_code == 2
    assert "osh auth login" not in result.stderr


@pytest.mark.parametrize(
    ("command", "changes"),
    [
        ("list", {"status": BuildStatus.UNKNOWN, "status_name": "done\u2028104  done  success"}),
        ("show", {"commit": replace(FAILED.commit, author="Ann\u2028104  done  success")}),
    ],
)
def test_a_line_separator_in_a_printed_field_does_not_add_a_row(
    client: type[StubClient], command: str, changes: dict[str, object]
):
    client.found = (replace(FAILED, **changes),)  # pyright: ignore[reportArgumentType]

    clean = run("builds", command, "--project", "acme", "--branch", "feature-x")
    client.found = (FAILED,)
    plain = run("builds", command, "--project", "acme", "--branch", "feature-x")

    assert len(clean.stdout.split("\n")) == len(plain.stdout.split("\n"))
    assert "\u2028" not in clean.stdout


@pytest.mark.parametrize("command", [("builds", "--help"), ("builds", "list", "--help"), ("builds", "show", "--help")])
def test_help_opens_neither_the_keyring_nor_the_network(client: type[StubClient], command: tuple[str, ...]):
    client.error = odouche.KeyringUnavailableError()

    result = run(*command)

    assert result.exit_code == 0
    assert "Example: osh builds" in click.unstyle(result.output)
