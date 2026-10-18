import json
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Self

import click
import pytest
from typer.testing import CliRunner, Result

import odouche
from odouche import Branch, Build, BuildResult, BuildStatus, Commit, Stage
from odouche_cli import _confirm, _time, builds
from odouche_cli._context import BRANCH_ENV, PROJECT_ENV
from odouche_cli.app import app


NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
FEATURE = Branch(id=4, name="feature-x", stage=Stage.DEVELOPMENT, stage_name="dev")

ESCAPE = "\x1b[2J\x1b]0;owned\x07"
URL = "https://acme-feature-x-104.dev.odoo.com"
ON_FEATURE = ("--project", "acme", "--branch", "feature-x")


def build(number: int, commit: str | None = None, **changes: object) -> Build:
    made = Build(
        id=number,
        name=f"acme-feature-x-{number}",
        branch_id=FEATURE.id,
        branch_name=FEATURE.name,
        commit=Commit(
            hash=commit or f"{number:x}".rjust(40, "a"),
            message="Add the report",
            author="Ann Example",
            timestamp=NOW - timedelta(hours=1),
            url="https://github.com/acme/odoo/commit/aaaa",
        ),
        status=BuildStatus.PROGRESS,
        status_name="progress",
        result=None,
        result_name=None,
        status_info="Installing: account",
        started_at=NOW - timedelta(seconds=80),
        url=f"https://acme-feature-x-{number}.dev.odoo.com",
    )
    return replace(made, **changes)  # pyright: ignore[reportArgumentType]


def done(made: Build, result: BuildResult | None = BuildResult.SUCCESS, **changes: object) -> Build:
    name = None if result is None else result.value
    values = {"status": BuildStatus.DONE, "status_name": "done", "status_info": "done", **changes}
    return replace(made, result=result, result_name=name, **values)  # pyright: ignore[reportArgumentType]


RUNNING = build(104)
TESTING = replace(RUNNING, status_info="Testing: account")
OLDER = done(build(103))

runner = CliRunner()


class Clock:
    """The deadline's clock, which only a sleep moves."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class StubClient:
    """Stands in for `odouche.Client`: lists scripted builds, and watches one through scripted changes."""

    project: odouche.Project = ACME
    listed: tuple[tuple[Build, ...], ...] = ((RUNNING, OLDER),)
    """What each request for the builds answers, the last one from then on."""
    changes: tuple[Build, ...] = (RUNNING, TESTING, done(RUNNING))
    ending: BaseException | None = None
    error: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""
    calls: list[str]
    watches: list[Iterator[Build]]
    """Every watch, kept so that only closing one ends it early."""
    closed: list[int]

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
        return [self.project]

    def branches(self, project: str) -> list[Branch]:
        self.calls.append(f"branches {project}")
        return [FEATURE]

    def builds(self, branch: Branch, *, limit: int = 4) -> list[Build]:
        asked = sum(call.startswith("builds") for call in self.calls)
        self.calls.append(f"builds {branch.name}")
        return list(self.listed[min(asked, len(self.listed) - 1)][:limit])

    def latest_build(self, branch: Branch) -> Build | None:
        self.calls.append(f"latest {branch.name}")
        return self.listed[-1][0]

    def watch_build(self, project: str, build: Build, *, timeout: float) -> Iterator[Build]:
        self.calls.append(f"watch {project} {build.id} {timeout:g}")
        if timeout <= 0:
            msg = "A timeout is more than 0"
            raise ValueError(msg)
        watch = self._watch(build)
        self.watches.append(watch)
        return watch

    def _watch(self, build: Build) -> Iterator[Build]:
        try:
            yield from self.changes
            if self.ending is not None:
                raise self.ending
        finally:
            self.closed.append(build.id)


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []
        calls: list[str] = []
        watches: list[Iterator[Build]] = []
        closed: list[int] = []

    monkeypatch.setattr(odouche, "Client", Client)
    monkeypatch.setattr(_time, "_now", lambda: NOW)
    return Client


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(builds, "_monotonic", clock.monotonic)
    monkeypatch.setattr(builds, "_sleep", clock.sleep)
    return clock


@pytest.fixture
def head(checkout: Callable[..., None]) -> str:
    """Make the current directory a checkout of `feature-x`, and return its HEAD."""
    checkout("feature-x", origin="git@github.com:acme/odoo.git")
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()  # noqa: S607


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def test_a_build_that_succeeds_exits_0_and_the_last_line_has_its_address(client: type[StubClient]):
    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.stdout == f"Build 104 succeeded: {URL}\n"
    assert client.calls == ["branches acme", "latest feature-x", "watch acme 104 1800"]


def test_a_build_that_fails_exits_20_and_names_the_logs(client: type[StubClient]):
    client.changes = (RUNNING, done(RUNNING, BuildResult.FAILED))

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 20
    assert result.stdout == f"Build 104 failed: {URL}\n"
    assert result.stderr.splitlines()[-1] == "Run `osh logs` to see why."


def test_a_build_that_ends_with_warnings_exits_21(client: type[StubClient]):
    client.changes = (RUNNING, done(RUNNING, BuildResult.WARNING))

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 21
    assert result.stdout == f"Build 104 finished with warnings: {URL}\n"
    assert "osh logs" not in result.stderr


@pytest.mark.parametrize(
    ("ended", "said"),
    [
        (done(RUNNING, None, status=BuildStatus.DROPPED, status_name="dropped", url=None), "(dropped)."),
        (done(RUNNING, None, status=BuildStatus.KILLED, status_name="killed"), f"(killed): {URL}"),
        (replace(done(RUNNING, BuildResult.UNKNOWN), result_name="flaky"), f"(flaky): {URL}"),
    ],
)
def test_a_build_that_ends_without_a_result_exits_22(client: type[StubClient], ended: Build, said: str):
    client.changes = (RUNNING, ended)

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 22
    assert result.stdout == f"Build 104 ended without a result {said}\n"


@pytest.mark.parametrize(
    ("result", "code", "said"),
    [(BuildResult.SUCCESS, 0, "succeeded"), (BuildResult.FAILED, 20, "failed")],
)
def test_a_dropped_build_exits_with_the_result_it_had_and_is_said_replaced_with_no_address(
    client: type[StubClient], result: BuildResult, code: int, said: str
):
    client.changes = (done(RUNNING, result, status=BuildStatus.DROPPED, status_name="dropped"),)

    watched = run("builds", "watch", *ON_FEATURE)

    assert watched.exit_code == code
    assert watched.stdout == f"Build 104 {said}, then was replaced by a newer build.\n"


def test_a_build_dropped_without_a_result_has_no_address(client: type[StubClient]):
    client.changes = (done(RUNNING, None, status=BuildStatus.DROPPED, status_name="dropped"),)

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 22
    assert result.stdout == "Build 104 ended without a result (dropped).\n"


def test_a_build_still_running_at_the_timeout_exits_23(client: type[StubClient]):
    client.changes = (RUNNING,)
    client.ending = odouche.StreamTimeoutError("The build was still being watched at the timeout.", operation="watch")

    result = run("builds", "watch", *ON_FEATURE, "--timeout", "60")

    assert result.exit_code == 23
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == "Timed out: build 104 has not finished."
    assert client.calls[-1] == "watch acme 104 60"


@pytest.mark.parametrize("timeout", ["0", "-5", "soon"])
def test_a_timeout_that_is_not_positive_is_a_usage_error(client: type[StubClient], timeout: str):
    result = run("builds", "watch", *ON_FEATURE, "--timeout", timeout)

    assert result.exit_code == 2
    assert client.calls == []


@pytest.mark.usefixtures("head")
def test_an_identifier_watches_that_build_and_waits_for_no_commit(client: type[StubClient]):
    client.changes = (OLDER,)

    result = run("builds", "watch", "103")

    assert result.exit_code == 0
    assert result.stdout == "Build 103 succeeded: https://acme-feature-x-103.dev.odoo.com\n"
    assert client.calls == ["projects", "branches acme", "builds feature-x", "watch acme 103 1800"]
    assert "Waiting" not in result.stderr


def test_it_waits_for_a_build_of_head_and_watches_that_one(client: type[StubClient], clock: Clock, head: str):
    pushed = build(105, head)
    client.listed = ((RUNNING, OLDER), (RUNNING, OLDER), (pushed, RUNNING, OLDER))
    client.changes = (pushed, done(pushed))

    result = run("builds", "watch")

    assert result.exit_code == 0
    assert result.stdout == "Build 105 succeeded: https://acme-feature-x-105.dev.odoo.com\n"
    assert result.stderr.splitlines()[0] == f"Waiting for a build of commit {head[:7]} on branch feature-x."
    assert result.stderr.count("Waiting") == 1
    assert clock.sleeps == [3.0, 3.0]
    assert client.calls == [
        "projects",
        "branches acme",
        "builds feature-x",
        "builds feature-x",
        "builds feature-x",
        "watch acme 105 1794",
    ]


def test_a_build_of_head_that_is_listed_is_watched_at_once(client: type[StubClient], clock: Clock, head: str):
    client.listed = ((RUNNING, build(102, head, status_info="Testing: stock")),)

    result = run("builds", "watch")

    assert client.calls[-1] == "watch acme 102 1800"
    assert clock.sleeps == []
    assert "Waiting" not in result.stderr


def test_no_build_of_head_by_the_timeout_exits_23_and_names_the_commit(
    client: type[StubClient], clock: Clock, head: str
):
    result = run("builds", "watch", "--timeout", "7")

    assert result.exit_code == 23
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == (
        f"Timed out: branch feature-x of acme has no build of commit {head[:7]}. "
        "Push it, or watch the latest build with --no-wait."
    )
    assert clock.sleeps == [3.0, 3.0, 1.0]
    assert not any(call.startswith("watch") for call in client.calls)


@pytest.mark.usefixtures("head")
def test_no_wait_watches_the_latest_build_whatever_its_commit(client: type[StubClient], clock: Clock):
    result = run("builds", "watch", "--no-wait")

    assert result.exit_code == 0
    assert client.calls == ["projects", "branches acme", "latest feature-x", "watch acme 104 1800"]
    assert clock.sleeps == []


def test_the_checkout_branch_named_by_the_flag_is_waited_on_for_head(client: type[StubClient], clock: Clock, head: str):
    pushed = build(105, head)
    client.listed = ((RUNNING, OLDER), (pushed, RUNNING, OLDER))
    client.changes = (done(pushed),)

    result = run("builds", "watch", "--branch", "feature-x")

    assert result.exit_code == 0
    assert clock.sleeps == [3.0]
    assert client.calls[-1] == "watch acme 105 1797"


def test_the_checkout_branch_named_by_the_environment_is_waited_on_for_head(
    client: type[StubClient], clock: Clock, head: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(BRANCH_ENV, "feature-x")
    pushed = build(105, head)
    client.listed = ((RUNNING, OLDER), (pushed, RUNNING, OLDER))
    client.changes = (done(pushed),)

    result = run("builds", "watch")

    assert result.exit_code == 0
    assert clock.sleeps == [3.0]
    assert client.calls[-1] == "watch acme 105 1797"


def test_another_branch_that_is_named_is_not_waited_on_for_head(
    client: type[StubClient], clock: Clock, checkout: Callable[..., None]
):
    checkout("main", origin="git@github.com:acme/odoo.git")

    result = run("builds", "watch", "--branch", "feature-x")

    assert result.exit_code == 0
    assert client.calls == ["projects", "branches acme", "latest feature-x", "watch acme 104 1800"]
    assert clock.sleeps == []
    assert result.stderr.splitlines()[0].startswith("Build 104:")


def test_a_checkout_of_another_repository_on_a_branch_of_that_name_is_not_waited_on_for_head(
    client: type[StubClient], clock: Clock, checkout: Callable[..., None]
):
    checkout("feature-x", origin="git@github.com:globex/erp.git")

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 0
    assert client.calls == ["branches acme", "projects", "latest feature-x", "watch acme 104 1800"]
    assert clock.sleeps == []
    assert "Waiting" not in result.stderr


def test_the_checkout_of_a_project_named_by_the_environment_is_waited_on_for_head(
    client: type[StubClient], clock: Clock, head: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(PROJECT_ENV, "acme")
    monkeypatch.setenv(BRANCH_ENV, "feature-x")
    pushed = build(105, head)
    client.listed = ((RUNNING, OLDER), (pushed, RUNNING, OLDER))
    client.changes = (done(pushed),)

    result = run("builds", "watch")

    assert result.exit_code == 0
    assert result.stderr.splitlines()[0] == f"Waiting for a build of commit {head[:7]} on branch feature-x."
    assert clock.sleeps == [3.0]
    assert client.calls == [
        "branches acme",
        "projects",
        "builds feature-x",
        "builds feature-x",
        "watch acme 105 1797",
    ]


@pytest.mark.usefixtures("head")
@pytest.mark.parametrize("arguments", [("--no-wait",), ("--commit", "a" * 7), ("104",)])
def test_a_named_project_is_not_looked_up_when_head_is_not_the_default(
    client: type[StubClient], arguments: tuple[str, ...]
):
    run("builds", "watch", *ON_FEATURE, *arguments)

    assert "projects" not in client.calls
    assert any(call.startswith("watch") for call in client.calls)


def test_an_unpushed_head_is_given_up_on_after_two_minutes_not_the_timeout(
    client: type[StubClient], clock: Clock, head: str
):
    result = run("builds", "watch")

    assert result.exit_code == 23
    assert head[:7] in result.stderr.splitlines()[-1]
    assert "--no-wait" in result.stderr.splitlines()[-1]
    assert clock.now == 120.0
    assert not any(call.startswith("watch") for call in client.calls)


def test_a_build_of_head_listed_by_the_last_request_allowed_is_still_watched(
    client: type[StubClient], clock: Clock, head: str
):
    pushed = build(105, head)
    client.listed = ((RUNNING, OLDER), (RUNNING, OLDER), (pushed, RUNNING, OLDER))
    client.changes = (done(pushed),)

    result = run("builds", "watch", "--timeout", "6")

    assert result.exit_code == 0
    assert clock.now == 6.0
    assert client.calls[-1] == "watch acme 105 3"


def test_a_head_that_cannot_be_read_is_said_and_the_latest_build_is_watched(
    client: type[StubClient], clock: Clock, git: Callable[..., None]
):
    git("init", "--quiet", "--initial-branch", "feature-x")
    git("remote", "add", "origin", "git@github.com:acme/odoo.git")

    result = run("builds", "watch")

    assert result.exit_code == 0
    assert result.stderr.splitlines()[0] == "HEAD could not be read: watching the branch's latest build."
    assert client.calls == ["projects", "branches acme", "latest feature-x", "watch acme 104 1800"]
    assert clock.sleeps == []


@pytest.mark.usefixtures("head")
def test_an_escape_sequence_in_the_project_name_is_not_emitted_while_waiting(client: type[StubClient]):
    client.project = replace(ACME, name=f"acme{ESCAPE}")

    result = run("builds", "watch", "--timeout", "1")

    assert result.exit_code == 23
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", result.output)
    assert "of acme[2J]0;owned has no build of commit" in result.stderr


def test_a_commit_is_waited_for_by_the_start_of_its_hash(client: type[StubClient]):
    theirs = build(102, "c0ffee1" + "b" * 33)
    client.listed = ((build(105, "b" * 5 + "c0ffee1" + "b" * 28), RUNNING, theirs),)
    client.changes = (done(theirs),)

    result = run("builds", "watch", *ON_FEATURE, "--commit", "C0FFEE1")

    assert result.exit_code == 0
    assert client.calls[-1] == "watch acme 102 1800"


def test_a_commit_can_be_a_whole_sha_256_hash(client: type[StubClient]):
    theirs = build(102, "c0ffee1" + "b" * 57)
    client.listed = ((RUNNING, theirs),)
    client.changes = (done(theirs),)

    result = run("builds", "watch", *ON_FEATURE, "--commit", theirs.commit.hash)

    assert result.exit_code == 0
    assert client.calls[-1] == "watch acme 102 1800"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--commit", "HEAD"),
        ("--commit", "c0ffe"),
        ("--commit", "c" * 65),
        ("--commit", "c0ffee1", "--no-wait"),
        ("104", "--commit", "c0ffee1"),
    ],
)
def test_a_commit_that_cannot_be_waited_for_is_a_usage_error(client: type[StubClient], arguments: tuple[str, ...]):
    result = run("builds", "watch", *ON_FEATURE, *arguments)

    assert result.exit_code == 2
    assert "--commit" in click.unstyle(result.stderr)
    assert client.calls == []


def test_as_json_stdout_is_one_object_for_each_change_and_nothing_else(client: type[StubClient]):
    client.changes = (RUNNING, TESTING, done(RUNNING, BuildResult.FAILED))

    result = run("--format", "json", "builds", "watch", *ON_FEATURE)

    assert result.exit_code == 20
    changes = [json.loads(line) for line in result.stdout.splitlines()]
    assert [(change["id"], change["status_info"], change["result"]) for change in changes] == [
        (104, "Installing: account", None),
        (104, "Testing: account", None),
        (104, "done", "failed"),
    ]
    assert changes[0]["commit"]["hash"] == RUNNING.commit.hash
    assert result.stderr == f"Build 104 failed: {URL}\nRun `osh logs` to see why.\n"


def test_off_a_terminal_each_change_is_a_line_on_stderr_with_the_time_the_build_has_run():
    result = run("builds", "watch", *ON_FEATURE)

    assert result.stderr.splitlines() == [
        "Build 104: progress, Installing: account (1:20)",
        "Build 104: progress, Testing: account (1:20)",
        "Build 104: done",
    ]


@pytest.mark.usefixtures("terminal")
def test_on_a_terminal_the_changes_are_one_line_that_is_redrawn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_confirm, "stderr_is_terminal", lambda: True)

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.stdout == f"Build 104 succeeded: {URL}\n"
    assert "\r" in result.stderr
    seen = [frame.strip() for frame in re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", result.stderr).split("\r")]
    frames = [frame for at, frame in enumerate(seen) if frame and frame != seen[at - 1]]
    assert frames[:2] == [
        "Build 104: progress, Installing: account (1:20)",
        "Build 104: progress, Testing: account (1:20)",
    ]
    # The one line break is taken back: up a line, and that line erased.
    assert result.stderr.count("\n") == 1
    tail = result.stderr.rpartition("\n")[2]
    assert "\x1b[1A" in tail
    assert "\x1b[2K" in tail


@pytest.mark.usefixtures("terminal")
def test_on_a_dumb_terminal_each_change_is_a_line(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(_confirm, "stderr_is_terminal", lambda: True)

    result = run("builds", "watch", *ON_FEATURE)

    assert result.stderr.splitlines() == [
        "Build 104: progress, Installing: account (1:20)",
        "Build 104: progress, Testing: account (1:20)",
        "Build 104: done",
    ]


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("variable", ["TTY_COMPATIBLE", "TTY_INTERACTIVE"])
def test_on_a_terminal_said_not_to_redraw_each_change_is_a_line(monkeypatch: pytest.MonkeyPatch, variable: str):
    monkeypatch.setenv(variable, "0")
    monkeypatch.setattr(_confirm, "stderr_is_terminal", lambda: True)

    result = run("builds", "watch", *ON_FEATURE)

    assert result.stderr.splitlines() == [
        "Build 104: progress, Installing: account (1:20)",
        "Build 104: progress, Testing: account (1:20)",
        "Build 104: done",
    ]


def test_a_build_waiting_for_a_worker_has_no_time(client: type[StubClient]):
    client.changes = (replace(RUNNING, started_at=None, status_info=None), done(RUNNING))

    result = run("builds", "watch", *ON_FEATURE)

    assert result.stderr.splitlines()[0] == "Build 104: progress"


def test_an_escape_sequence_in_what_the_build_is_doing_is_not_emitted(client: type[StubClient]):
    client.changes = (replace(RUNNING, status_info=f"Installing{ESCAPE}\x9b31m"), done(RUNNING, url=f"{URL}{ESCAPE}"))

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 0
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", result.output)
    assert "Build 104: progress, Installing[2J]0;owned31m (1:20)" in result.stderr


def test_an_interrupt_exits_130_closes_the_watch_and_asks_for_nothing_more(
    client: type[StubClient], monkeypatch: pytest.MonkeyPatch
):
    def interrupted(_: Build) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builds, "_progress", interrupted)

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 130
    assert result.stdout == ""
    assert client.closed == [104]
    assert client.calls[-1] == "watch acme 104 1800"


def test_a_watch_that_is_not_a_generator_is_closed_too(client: type[StubClient], monkeypatch: pytest.MonkeyPatch):
    class Watch:
        def __init__(self) -> None:
            self.changes = iter((RUNNING, done(RUNNING)))

        def __iter__(self) -> Self:
            return self

        def __next__(self) -> Build:
            return next(self.changes)

        def close(self) -> None:
            client.closed.append(104)

    monkeypatch.setattr(client, "watch_build", lambda *_, **__: Watch())

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 0
    assert client.closed == [104]


def test_logged_out_exits_3_and_names_the_login(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("builds", "watch", *ON_FEATURE)

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "osh auth login" in result.stderr


def test_help_opens_neither_the_keyring_nor_the_network(client: type[StubClient]):
    client.error = odouche.KeyringUnavailableError()

    result = run("builds", "watch", "--help")

    assert result.exit_code == 0
    assert "Example: git push && osh builds watch" in click.unstyle(result.output)
