import os
import shutil
import sys
from typing import Self

import pytest
from typer.testing import CliRunner

import odouche
from odouche import Branch, Build, BuildStatus, Commit, SshTarget, Stage
from odouche_cli import ssh
from odouche_cli.app import app


ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
FEATURE = Branch(id=4, name="feature-x", stage=Stage.DEVELOPMENT, stage_name="dev")
BUILD = Build(
    id=104,
    name="acme-feature-x-104",
    branch_id=FEATURE.id,
    branch_name=FEATURE.name,
    commit=Commit(hash="a" * 40, message="Start", author="Ann Example", timestamp=None, url=""),  # pyright: ignore[reportArgumentType]
    status=BuildStatus.DONE,
    status_name="done",
    result=None,
    result_name=None,
    status_info=None,
    started_at=None,
    url="https://acme-feature-x-104.dev.odoo.com",
)
HOST = "acme-feature-x-104.dev.odoo.com"
PROGRAM = "/usr/bin/ssh"
SESSION = "s3ss10n-v4lu3"

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: answers with one branch, its build and the build's target."""

    latest: Build | None = BUILD
    refused: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""
    events: list[object]

    def __init__(self, *, read_only: bool = False) -> None:
        self.modes.append(read_only)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.events.append("closed")

    def projects(self) -> list[odouche.Project]:
        return [ACME]

    def branches(self, project: str) -> list[Branch]:
        assert project == ACME.name
        return [FEATURE]

    def latest_build(self, branch: Branch) -> Build | None:
        assert branch == FEATURE
        return self.latest

    def ssh_target(self, build: Build) -> SshTarget:
        assert build == BUILD
        if self.refused is not None:
            raise self.refused
        return SshTarget(user=str(build.id), host=HOST)


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    made = type("Made", (StubClient,), {"modes": [], "events": []})
    monkeypatch.setattr(odouche, "Client", made)
    return made


@pytest.fixture(autouse=True)
def executed(monkeypatch: pytest.MonkeyPatch, client: type[StubClient]) -> list[object]:
    """Stand in for the exec, and return the client's events, where each exec is kept with the session it saw."""

    def record(program: str, command: list[str]) -> None:
        client.events.append((program, command, os.environ.get(odouche.SESSION_ENV)))

    monkeypatch.setattr(ssh, "_exec", record)
    monkeypatch.setattr(shutil, "which", lambda name: PROGRAM if name == "ssh" else None)
    return client.events


def run(*args: str):
    return runner.invoke(app, ["ssh", "--project", "acme", "--branch", "feature-x", *args])


def test_the_users_ssh_is_executed_with_the_user_and_the_host_of_the_build(executed: list[object]):
    result = run()

    assert (result.exit_code, result.output) == (0, "")
    # No key and no option but the user: the rest is the user's own configuration.
    assert executed == ["closed", (PROGRAM, ["ssh", "-l", "104", HOST], None)]


def test_a_target_odoo_sh_gives_that_is_no_host_executes_nothing(client: type[StubClient], executed: list[object]):
    client.refused = odouche.UpstreamChangedError("ssh target", "url")

    result = run()

    assert result.exit_code == 6
    assert executed == ["closed"]


@pytest.mark.parametrize(
    "args",
    [
        ["ls", "-la"],
        ["odoo-bin", "--help"],
        ["ls", "--branch", "other"],
        ["--", "-L", "8069:localhost:8069"],
        ["--", "-N", "-v"],
        ["--", "odoo-bin", "shell", "--", "-x"],
    ],
)
def test_the_arguments_are_given_to_ssh_after_the_host_as_they_are(executed: list[object], args: list[str]):
    result = run(*args)

    given = args[1:] if args[0] == "--" else args
    assert result.exit_code == 0
    assert executed[-1] == (PROGRAM, ["ssh", "-l", "104", HOST, *given], None)


def test_an_option_for_ssh_that_comes_first_and_not_after_two_dashes_is_a_usage_error(executed: list[object]):
    result = run("-L", "8069:localhost:8069")

    assert result.exit_code == 2
    assert executed == []


def test_the_session_is_not_left_in_the_environment_of_ssh(monkeypatch: pytest.MonkeyPatch, executed: list[object]):
    monkeypatch.setenv(odouche.SESSION_ENV, SESSION)

    result = run()

    assert result.exit_code == 0
    assert executed[-1] == (PROGRAM, ["ssh", "-l", "104", HOST], None)
    assert SESSION not in result.output


def test_the_branch_is_the_one_of_the_checkout_when_none_is_given(checkout, executed: list[object]):
    checkout("feature-x")

    result = runner.invoke(app, ["ssh", "--project", "acme"])

    assert result.exit_code == 0
    assert executed[-1] == (PROGRAM, ["ssh", "-l", "104", HOST], None)


def test_a_branch_with_no_build_executes_nothing(client: type[StubClient], executed: list[object]):
    client.latest = None

    result = run()

    assert result.exit_code == 4
    assert "has no build" in result.output
    assert executed == ["closed"]


def test_without_ssh_the_command_to_run_is_shown_and_nothing_is_executed(
    monkeypatch: pytest.MonkeyPatch, executed: list[object]
):
    monkeypatch.setattr(shutil, "which", lambda _: None)

    result = run("--", "echo", "a b")

    assert result.exit_code == 14
    assert f"ssh -l 104 {HOST} echo 'a b'" in result.output
    assert executed == ["closed"]


def test_on_windows_the_command_to_run_is_shown_and_nothing_is_executed(
    monkeypatch: pytest.MonkeyPatch, executed: list[object]
):
    monkeypatch.setattr(sys, "platform", "win32")

    result = run("--", "echo", "a b")

    assert result.exit_code == 14
    assert f'on Windows. Run it yourself: ssh -l 104 {HOST} echo "a b"' in result.output
    assert executed == ["closed"]
