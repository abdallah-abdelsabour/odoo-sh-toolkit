import json
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import click
import pytest
import typer
from typer.testing import CliRunner, Result

import odouche
from odouche import Branch, Build, BuildResult, BuildStatus, Commit, Log, LogKind, LogLine, Stage
from odouche_cli import _confirm, _time, builds, ssh
from odouche_cli._context import BRANCH_ENV, PROJECT_ENV
from odouche_cli.app import app


NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
MAIN = Branch(id=1, name="main", stage=Stage.PRODUCTION, stage_name="production")
FEATURE = Branch(id=4, name="feature-x", stage=Stage.DEVELOPMENT, stage_name="dev")
SESSION = odouche.SessionInfo(odouche.SessionSource.KEYRING, NOW - timedelta(days=3), NOW + timedelta(days=27))

ESCAPE = "\x1b[2J\x1b]0;owned\x07"
URL = "https://acme-feature-x-105.dev.odoo.com"
ON_FEATURE = ("--project", "acme", "--branch", "feature-x")
REBUILD = ("builds", "rebuild")
ALLOW = ("firewall", "allow")


def build(number: int, **changes: object) -> Build:
    made = Build(
        id=number,
        name=f"acme-feature-x-{number}",
        branch_id=FEATURE.id,
        branch_name=FEATURE.name,
        commit=Commit(
            hash="c0ffee1".ljust(40, "a"),
            message="Add the report\n\nWith its tests.",
            author="Ann Example",
            timestamp=NOW - timedelta(hours=1),
            url="https://github.com/acme/odoo/commit/c0ffee1",
        ),
        status=BuildStatus.PROGRESS,
        status_name="progress",
        result=None,
        result_name=None,
        status_info="Installing: account",
        started_at=NOW - timedelta(seconds=5),
        url=f"https://acme-feature-x-{number}.dev.odoo.com",
    )
    return replace(made, **changes)  # pyright: ignore[reportArgumentType]


def done(made: Build, result: BuildResult = BuildResult.SUCCESS) -> Build:
    return replace(
        made, status=BuildStatus.DONE, status_name="done", status_info="done", result=result, result_name=result.value
    )


LATEST = done(build(104), BuildResult.FAILED)
STARTED = build(105)
LOST = odouche.OutcomeUnknownError(
    "The rebuild of branch 4 was sent, and its build was not found. Look before trying again."
)

# The checks themselves, which a fixture replaces.
IS_TERMINAL = {"stdin": _confirm.stdin_is_terminal, "stderr": _confirm.stderr_is_terminal}

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: rebuilds as the library does, and answers what every command reads."""

    branch: Branch = FEATURE
    latest: Build | None = LATEST
    changes: tuple[Build, ...] = (STARTED, done(STARTED))
    """What a watch of the new build goes through."""
    lost: odouche.OdoucheError | None = None
    """What a rebuild raises once it is sent."""
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

    @property
    def session(self) -> odouche.SessionInfo:
        return SESSION

    def identity(self) -> odouche.Identity:
        return odouche.Identity(
            user_id=7, name="Ada Lovelace", username="ada", email="ada@example.com", session=SESSION
        )

    def projects(self) -> list[odouche.Project]:
        return [ACME]

    def branches(self, project: str) -> list[Branch]:
        self.calls.append(f"branches {project}")
        return [self.branch]

    def builds(self, branch: Branch, *, limit: int = 4) -> list[Build]:
        return [] if self.latest is None else [self.latest][:limit]

    def latest_build(self, branch: Branch) -> Build | None:
        self.calls.append(f"latest {branch.name}")
        return self.latest

    # The library's own, which asks nothing.
    check_rebuild = odouche.Client.check_rebuild
    ssh_target = odouche.Client.ssh_target
    plan_allow_ip = odouche.Client.plan_allow_ip

    def firewall(self, project: str) -> odouche.Firewall:
        return odouche.Firewall(project, ())

    def allow_ip(self, project: str, address: str, *, replace: bool = True) -> odouche.Firewall:
        return self.plan_allow_ip(self.firewall(project), address, replace=replace)

    def rebuild(self, branch: Branch | int) -> Build:
        self.calls.append(f"rebuild {branch.name if isinstance(branch, Branch) else branch}")
        if self.lost is not None:
            raise self.lost
        return STARTED

    def watch_build(self, project: str, build: Build, *, timeout: float) -> Iterator[Build]:
        self.calls.append(f"watch {project} {build.id} {timeout:g}")
        yield from self.changes

    def logs(self, project: str, build: Build) -> list[Log]:
        return [Log(kind=LogKind.INSTALL, name="install", modified_at=NOW, size="2 KB")]

    def read_log(
        self, project: str, build: Build, kind: LogKind | str, *, tail: int | None = None
    ) -> Iterator[LogLine]:
        yield LogLine(text="Modules loaded.", offset=16, truncated=False)


@pytest.fixture(autouse=True)
def read_only_clients() -> None:
    """Replace the shared check: the command of this file is the one that writes."""


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []
        calls: list[str] = []

    monkeypatch.setattr(odouche, "Client", Client)
    monkeypatch.setattr(_time, "_now", lambda: NOW)
    # The usual case. A test of a script's run turns one off.
    monkeypatch.setattr(_confirm, "stdin_is_terminal", lambda: True)
    monkeypatch.setattr(_confirm, "stderr_is_terminal", lambda: True)
    return Client


@pytest.fixture(params=["stdin_is_terminal", "stderr_is_terminal"], ids=["stdin", "stderr"])
def unattended(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Make stdin, then stderr, something else than a terminal."""
    monkeypatch.setattr(_confirm, request.param, lambda: False)


def run(*args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=stdin)


def sent(client: type[StubClient]) -> list[str]:
    return [call for call in client.calls if call.startswith("rebuild")]


def test_a_yes_sends_one_rebuild_and_prints_the_new_build(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, stdin="y\n")

    assert result.exit_code == 0
    assert sent(client) == ["rebuild feature-x"]
    assert client.calls == ["branches acme", "latest feature-x", "rebuild feature-x"]
    shown = dict(line.split(maxsplit=1) for line in result.stdout.splitlines() if len(line.split()) > 1)
    assert shown["ID"] == "105"
    assert shown["Status"] == "progress"
    assert shown["Subject"] == "Add the report"
    assert shown["URL"] == URL


@pytest.mark.parametrize("answer", ["Y\n", "yes\n", " YES \n", "y"])
def test_a_yes_is_y_or_yes_in_any_case(client: type[StubClient], answer: str):
    result = run(*REBUILD, *ON_FEATURE, stdin=answer)

    assert result.exit_code == 0
    assert sent(client) == ["rebuild feature-x"]


@pytest.mark.parametrize("answer", ["n\n", "\n", "no\n", "yep\n", "1\n", "true\n", ""], ids=repr)
def test_anything_but_a_yes_sends_nothing_and_exits_1(client: type[StubClient], answer: str):
    result = run(*REBUILD, *ON_FEATURE, stdin=answer)

    assert result.exit_code == 1
    assert sent(client) == []
    assert result.stdout == ""
    # The line break of the answer is the terminal's to echo.
    assert result.stderr.endswith("Start a new build of this branch? [y/N]: Nothing was sent.\n")


def test_an_interrupt_at_the_question_exits_130_and_sends_nothing(
    client: type[StubClient], monkeypatch: pytest.MonkeyPatch
):
    def interrupted(_: str) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(_confirm, "confirmed", interrupted)

    result = run(*REBUILD, *ON_FEATURE)

    assert result.exit_code == 130
    assert sent(client) == []


@pytest.mark.parametrize("name", ["stdin", "stderr"])
def test_a_closed_stream_is_not_a_terminal(monkeypatch: pytest.MonkeyPatch, name: str):
    monkeypatch.setattr(sys, name, None)

    assert not IS_TERMINAL[name]()


def test_yes_sends_the_rebuild_without_asking(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, "--yes")

    assert result.exit_code == 0
    assert sent(client) == ["rebuild feature-x"]
    assert "[y/N]" not in result.stderr


@pytest.mark.usefixtures("unattended")
def test_a_run_nobody_attends_is_refused_before_a_client_is_opened(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, stdin="y\n")

    assert result.exit_code == 2
    assert client.modes == []
    assert sent(client) == []
    assert result.stdout == ""
    assert "Stdin or stderr is not a terminal" in click.unstyle(result.stderr)
    assert "--yes" in click.unstyle(result.stderr)


@pytest.mark.usefixtures("unattended")
def test_a_run_nobody_attends_rebuilds_with_yes(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, "-y")

    assert result.exit_code == 0
    assert sent(client) == ["rebuild feature-x"]


@pytest.mark.parametrize("arguments", [(), ("--yes",)])
def test_what_is_rebuilt_is_said_before_anything_is_sent(client: type[StubClient], arguments: tuple[str, ...]):
    result = run(*REBUILD, *ON_FEATURE, *arguments, stdin="n\n")

    assert result.stderr.splitlines()[:3] == [
        "Project: acme",
        "Branch: feature-x (development)",
        "Latest build: 104, of commit c0ffee1 Add the report",
    ]


def test_the_question_comes_after_what_is_rebuilt(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, stdin="n\n")

    assert result.stderr.splitlines()[3].startswith("Start a new build of this branch? [y/N]:")


def test_a_stage_the_library_does_not_know_exits_9_and_sends_nothing(client: type[StubClient]):
    client.branch = replace(FEATURE, stage=Stage.UNKNOWN, stage_name="preview")

    result = run(*REBUILD, *ON_FEATURE, "--yes")

    assert result.exit_code == 9
    assert client.calls == ["branches acme"]


def test_a_branch_with_no_build_is_rebuilt_too(client: type[StubClient]):
    client.latest = None

    result = run(*REBUILD, *ON_FEATURE, "--yes")

    assert result.exit_code == 0
    assert result.stderr.splitlines()[2] == "Latest build: none"
    assert sent(client) == ["rebuild feature-x"]


def test_an_escape_sequence_in_the_subject_is_not_emitted(client: type[StubClient]):
    commit = replace(LATEST.commit, message=f"Add{ESCAPE} the report\x9b31m")
    client.latest = replace(LATEST, commit=commit)

    result = run(*REBUILD, *ON_FEATURE, stdin="n\n")

    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", result.output)
    assert "Latest build: 104, of commit c0ffee1 Add[2J]0;owned the report31m" in result.stderr


def test_watch_sends_one_rebuild_then_watches_the_new_build_and_exits_0(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, "--yes", "--watch")

    assert result.exit_code == 0
    assert client.calls == ["branches acme", "latest feature-x", "rebuild feature-x", "watch acme 105 1800"]
    assert result.stdout == f"Build 105 succeeded: {URL}\n"
    assert "Build 105 was started." in result.stderr.splitlines()


def test_the_new_build_is_named_on_stderr_before_it_is_shown(client: type[StubClient], monkeypatch: pytest.MonkeyPatch):
    def fails() -> list[object]:
        msg = "No columns."
        raise ValueError(msg)

    monkeypatch.setattr(builds, "_build_columns", fails)

    result = run(*REBUILD, *ON_FEATURE, "--yes")

    assert result.exit_code == 1
    assert result.stderr.splitlines()[3] == "Build 105 was started."


@pytest.mark.parametrize(
    ("error", "code", "hint"),
    [
        (odouche.UpstreamUnavailableError("Odoo.sh cannot be reached."), 7, "Try again later."),
        (odouche.SessionExpiredError("The session has expired."), 3, "Run `osh auth login`."),
    ],
    ids=["unavailable", "expired"],
)
def test_a_watch_that_fails_names_the_build_to_watch_and_does_not_rebuild_again(
    client: type[StubClient], monkeypatch: pytest.MonkeyPatch, error: odouche.OdoucheError, code: int, hint: str
):
    def failing(*_: object, **__: object) -> Iterator[Build]:
        yield STARTED
        raise error

    monkeypatch.setattr(client, "watch_build", failing)

    result = run(*REBUILD, *ON_FEATURE, "--yes", "--watch")

    assert result.exit_code == code
    assert sent(client) == ["rebuild feature-x"]
    assert result.stderr.splitlines()[-3:] == [
        "Build 105 may still be running. Follow it with `osh builds watch 105`.",
        str(error),
        hint,
    ]


@pytest.mark.parametrize(
    ("result_of_build", "code"), [(BuildResult.FAILED, 20), (BuildResult.WARNING, 21)], ids=["failed", "warning"]
)
def test_watch_exits_with_the_code_of_the_watch(client: type[StubClient], result_of_build: BuildResult, code: int):
    client.changes = (STARTED, done(STARTED, result_of_build))

    result = run(*REBUILD, *ON_FEATURE, "--yes", "--watch")

    assert result.exit_code == code
    assert sent(client) == ["rebuild feature-x"]


def test_watch_that_times_out_exits_23_and_does_not_rebuild_again(
    client: type[StubClient], monkeypatch: pytest.MonkeyPatch
):
    def late(*_: object, **__: object) -> Iterator[Build]:
        yield STARTED
        msg = "Still running."
        raise odouche.StreamTimeoutError(msg)

    monkeypatch.setattr(client, "watch_build", late)

    result = run(*REBUILD, *ON_FEATURE, "--yes", "--watch")

    assert result.exit_code == 23
    assert sent(client) == ["rebuild feature-x"]


def test_timeout_is_how_long_the_watch_lasts(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, "--yes", "--watch", "--timeout", "60")

    assert result.exit_code == 0
    assert client.calls[-1] == "watch acme 105 60"


def test_timeout_without_watch_is_a_usage_error(client: type[StubClient]):
    result = run(*REBUILD, *ON_FEATURE, "--yes", "--timeout", "60")

    assert result.exit_code == 2
    assert "--watch" in click.unstyle(result.stderr)
    assert client.modes == []


def test_json_is_the_new_build(client: type[StubClient]):
    result = run("--format", "json", *REBUILD, *ON_FEATURE, "--yes")

    assert result.exit_code == 0
    found = json.loads(result.stdout)
    assert found["id"] == 105
    assert found["commit"]["message"] == "Add the report\n\nWith its tests."
    assert found["url"] == URL


def test_json_with_watch_is_one_build_per_change(client: type[StubClient]):
    client.changes = (STARTED, done(STARTED, BuildResult.FAILED))

    result = run("--format", "json", *REBUILD, *ON_FEATURE, "--yes", "--watch")

    assert result.exit_code == 20
    changes = [json.loads(line) for line in result.stdout.splitlines()]
    assert [(change["id"], change["result"]) for change in changes] == [(105, None), (105, "failed")]


def test_an_unknown_outcome_exits_10_names_the_builds_to_check_and_is_not_sent_again(client: type[StubClient]):
    client.lost = LOST

    result = run(*REBUILD, *ON_FEATURE, "--yes", "--watch")

    assert result.exit_code == 10
    assert sent(client) == ["rebuild feature-x"]
    assert result.stdout == ""
    assert result.stderr.splitlines()[-2:] == [
        "The rebuild of branch 4 was sent, and its build was not found. Look before trying again.",
        "`osh builds list` shows whether the build was started.",
    ]
    assert not any(call.startswith("watch") for call in client.calls)


@pytest.mark.parametrize("arguments", [(), ("--yes",)], ids=["asked", "yes"])
def test_a_production_branch_exits_9_with_the_message_of_the_library_and_no_question(
    client: type[StubClient], arguments: tuple[str, ...]
):
    client.branch = MAIN

    result = run(*REBUILD, "--project", "acme", "--branch", "main", *arguments, stdin="y\n")

    assert result.exit_code == 9
    assert client.calls == ["branches acme"]
    assert result.stdout == ""
    assert result.stderr == "A branch in the production stage is not rebuilt: only a development or a staging one is.\n"


def test_the_client_is_one_that_writes(client: type[StubClient]):
    run(*REBUILD, *ON_FEATURE, "--yes")

    assert client.modes == [False]


def test_logged_out_exits_3_and_names_the_login(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run(*REBUILD, *ON_FEATURE, "--yes")

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "osh auth login" in result.stderr


def test_a_missing_branch_while_logged_out_is_a_usage_error(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run(*REBUILD, "--project", "acme", "--yes")

    assert result.exit_code == 2
    assert BRANCH_ENV in result.stderr
    assert client.modes == []


def test_help_opens_no_client_and_its_first_line_says_it_changes_state(client: type[StubClient]):
    client.error = odouche.KeyringUnavailableError()

    result = run(*REBUILD, "--help")

    assert result.exit_code == 0
    lines = [line.strip() for line in click.unstyle(result.output).splitlines() if line.strip()]
    assert lines[1].startswith("Change state on Odoo.sh:")
    assert client.modes == []


def test_the_help_of_the_group_says_it_changes_state(client: type[StubClient]):
    result = run("builds", "--help")

    listed = [line for line in click.unstyle(result.output).splitlines() if " rebuild " in line]
    assert len(listed) == 1
    assert "Change state on Odoo.sh" in listed[0]


def commands(command: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """Walk a command's tree down to the commands that run something."""
    children: dict[str, Any] | None = getattr(command, "commands", None)
    if children is None:
        yield path
        return
    for name, child in children.items():
        yield from commands(child, (*path, name))


COMMANDS = sorted(commands(typer.main.get_command(app)))
# What a command needs to run to its end, when the project and the branch in the environment are not enough.
ARGUMENTS: dict[tuple[str, ...], tuple[str, ...]] = {REBUILD: ("--yes",), ALLOW: ("9.9.9.9", "--yes")}
# The commands that change state on Odoo.sh.
WRITERS = {REBUILD, ALLOW}
# The commands that build no client.
CLIENTLESS = {("auth", "logout")}


def test_the_tree_has_the_commands_that_write():
    assert set(COMMANDS) > WRITERS
    assert set(COMMANDS) > CLIENTLESS


@pytest.mark.parametrize("path", COMMANDS, ids=" ".join)
def test_only_the_commands_that_change_state_build_a_client_that_writes(
    client: type[StubClient], monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
):
    monkeypatch.setenv(PROJECT_ENV, "acme")
    monkeypatch.setenv(BRANCH_ENV, "feature-x")
    monkeypatch.delenv(odouche.SESSION_ENV, raising=False)
    monkeypatch.setattr(odouche, "login", lambda **_: None)
    monkeypatch.setattr(ssh, "_exec", lambda *_: None)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/ssh")
    monkeypatch.setattr(odouche, "logout", lambda: odouche.LogoutResult(None, False, False, None))  # noqa: FBT003

    result = run(*path, *ARGUMENTS.get(path, ()))

    # A command that did not run to its end shows nothing: a new one may need a row in `ARGUMENTS`.
    assert result.exit_code == 0, result.output
    if path in CLIENTLESS:
        assert client.modes == []
    else:
        assert client.modes
        assert set(client.modes) == {path not in WRITERS}
