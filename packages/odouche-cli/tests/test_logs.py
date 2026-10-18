import io
import json
import math
import os
import re
import select
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self, override

import click
import pytest
from typer.testing import CliRunner, Result

import odouche
from odouche import Branch, Build, BuildResult, BuildStatus, Commit, Log, LogKind, LogLine, Stage
from odouche_cli import _output, _time
from odouche_cli.app import app


NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
FEATURE = Branch(id=4, name="feature-x", stage=Stage.DEVELOPMENT, stage_name="dev")
ON_FEATURE = ("--project", "acme", "--branch", "feature-x")


def build(number: int) -> Build:
    return Build(
        id=number,
        name=f"acme-feature-x-{number}",
        branch_id=FEATURE.id,
        branch_name=FEATURE.name,
        commit=Commit(
            hash=f"{number:x}".rjust(40, "a"),
            message="Add the report",
            author="Ann Example",
            timestamp=NOW - timedelta(hours=1),
            url="https://github.com/acme/odoo/commit/aaaa",
        ),
        status=BuildStatus.DONE,
        status_name="done",
        result=BuildResult.FAILED,
        result_name="failed",
        status_info=None,
        started_at=NOW - timedelta(minutes=5),
        url=f"https://acme-feature-x-{number}.dev.odoo.com",
    )


LATEST = build(104)
OLDER = build(103)

INSTALL = tuple(f"install {number}" for number in range(1, 121))
PIP = ("Collecting acme-reports", "Successfully installed acme-reports-1.0")
SHELL = ("shell 1", "shell 2")
WRITTEN = ("written 1", "written 2")
# A screen cleared, a window title set, a C1 sequence and control characters, in an indented line.
HOSTILE = "\tFile \x1b[2J\x1b]0;owned\x07\x9b31m\rdone\x7f\x00\x08\x0b\x0c"
STRIPPED = "\tFile done"
MARKER = "the-log-holds-this"

LISTED = (
    Log(kind=LogKind.INSTALL, name="install", modified_at=NOW - timedelta(minutes=3), size="156 KB"),
    Log(kind=LogKind.PIP, name="pip", modified_at=NOW - timedelta(hours=2), size="2 KB"),
    Log(kind=LogKind.UNKNOWN, name="shell", modified_at=NOW - timedelta(days=1), size="0 B"),
)
# What a staging build has.
STAGING = tuple(
    Log(kind=LogKind(name), name=name, modified_at=NOW - timedelta(minutes=3), size="2 KB")
    for name in ("odoo", "update", "neutralize")
)
TIMEOUT = odouche.StreamTimeoutError("The log was still being followed at the timeout.", operation="logs")

runner = CliRunner()


def log_lines(texts: tuple[str, ...]) -> list[LogLine]:
    made: list[LogLine] = []
    offset = 0
    for text in texts:
        offset += len(text.encode()) + 1
        made.append(LogLine(text=text, offset=offset, truncated=False))
    return made


class StubClient:
    """Stands in for `odouche.Client`: reads scripted logs, and follows one through scripted lines."""

    listed: tuple[Log, ...] = LISTED
    content: dict[str, tuple[str, ...]] = {"install": INSTALL, "pip": PIP, "shell": SHELL}
    written: tuple[str, ...] = WRITTEN
    """What is added to a log while it is followed."""
    ending: BaseException | None = None
    """What ends a follow that nothing closed, in place of its timeout, or of an interrupt when it has none."""
    error: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""
    pause: Callable[[], object] = staticmethod(lambda: None)
    """Called once the first line of a log has been taken."""
    calls: list[str]
    streams: list[Iterator[LogLine]]
    """Every read and follow, kept so that only closing one ends it early."""
    closed: list[str]

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
        return [FEATURE]

    def builds(self, branch: Branch, *, limit: int = 4) -> list[Build]:
        self.calls.append(f"builds {branch.name}")
        return [LATEST, OLDER][:limit]

    def latest_build(self, branch: Branch) -> Build | None:
        self.calls.append(f"latest {branch.name}")
        return LATEST

    def logs(self, project: str, build: Build) -> list[Log]:
        self.calls.append(f"logs {project} {build.id}")
        return list(self.listed)

    def read_log(
        self, project: str, build: Build, kind: LogKind | str, *, tail: int | None = None
    ) -> Iterator[LogLine]:
        self.calls.append(f"read {project} {build.id} {kind} {tail}")
        if tail is not None and tail < 1:
            msg = "A tail is at least 1"
            raise ValueError(msg)
        lines = self._content(build, kind)
        return self._stream("read", lines if tail is None else lines[-tail:], None)

    def follow_log(
        self,
        project: str,
        build: Build,
        kind: LogKind | str,
        *,
        timeout: float,
        tail: int = 0,
        offset: int | None = None,
    ) -> Iterator[LogLine]:
        self.calls.append(f"follow {project} {build.id} {kind} {timeout:g} {tail} {offset}")
        if timeout <= 0:
            msg = "A timeout is more than 0"
            raise ValueError(msg)
        if tail < 0 or (offset is not None and offset < 0):
            msg = "A tail and an offset are at least 0"
            raise ValueError(msg)
        lines = log_lines(self.content[str(kind)] + self.written) if str(kind) in self.content else []
        held = len(self._content(build, kind))
        if offset is None:
            first = held - tail if tail else held
            lines = lines[max(first, 0) :]
        else:
            lines = [line for line in lines if line.offset > offset]
        return self._stream("follow", lines, self.ending or (TIMEOUT if timeout < math.inf else KeyboardInterrupt()))

    def _content(self, build: Build, kind: LogKind | str) -> list[LogLine]:
        if str(kind) not in self.content:
            msg = f"Build {build.id} has no log named {str(kind)!r}."
            raise odouche.NotFoundError(msg)
        return log_lines(self.content[str(kind)])

    def _stream(self, name: str, lines: list[LogLine], ending: BaseException | None) -> Iterator[LogLine]:
        stream = self._lines(name, lines, ending)
        self.streams.append(stream)
        return stream

    def _lines(self, name: str, lines: list[LogLine], ending: BaseException | None) -> Iterator[LogLine]:
        try:
            for at, line in enumerate(lines):
                yield line
                if at == 0:
                    self.pause()
            if ending is not None:
                raise ending
        finally:
            self.closed.append(name)


def fresh() -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []
        calls: list[str] = []
        streams: list[Iterator[LogLine]] = []
        closed: list[str] = []

    return Client


class Stdout(io.TextIOBase):
    """A stdout that keeps what each flush sends, and fails from the flush numbered `fails_at`."""

    encoding: str = "utf-8"  # pyright: ignore[reportIncompatibleMethodOverride]

    def __init__(self, fails_at: int | None = None, error: type[BaseException] = BrokenPipeError) -> None:
        self.flushed: list[str] = []
        self._pending = ""
        self._fails_at = fails_at
        self._error = error

    @override
    def write(self, s: str, /) -> int:
        self._pending += s
        return len(s)

    @override
    def flush(self) -> None:
        pending, self._pending = self._pending, ""
        if not pending:
            return
        if self._fails_at is not None and len(self.flushed) >= self._fails_at:
            raise self._error
        self.flushed.append(pending)


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    made = fresh()
    monkeypatch.setattr(odouche, "Client", made)
    monkeypatch.setattr(_time, "_now", lambda: NOW)
    return made


@pytest.fixture
def stdout(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Stdout]:
    """Make the lines go to a `Stdout`."""

    def stdout(fails_at: int | None = None, error: type[BaseException] = BrokenPipeError) -> Stdout:
        made = Stdout(fails_at, error)
        monkeypatch.setattr(_output, "_stdout", lambda: made)
        return made

    return stdout


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def printed(*lines: str) -> str:
    return "".join(f"{line}\n" for line in lines)


def test_it_prints_the_last_hundred_lines_of_the_install_log_of_the_latest_build(client: type[StubClient]):
    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.stdout == printed(*INSTALL[-100:])
    assert result.stderr == ""
    assert client.calls == ["branches acme", "latest feature-x", "read acme 104 install 100"]
    assert client.closed == ["read"]


@pytest.mark.parametrize(
    ("mode", "asked"),
    [((), "read acme 104 {} 100"), (("--follow", "--timeout", "60"), "follow acme 104 {} 60 100 None")],
    ids=["read", "follow"],
)
def test_a_build_with_no_install_log_prints_its_odoo_log(client: type[StubClient], mode: tuple[str, ...], asked: str):
    client.listed = STAGING
    client.content = {"odoo": ("odoo 1", "odoo 2"), "update": ("update 1",)}
    client.written = ()

    result = run("logs", *ON_FEATURE, *mode)

    assert result.stdout == printed("odoo 1", "odoo 2")
    assert client.calls[2:] == [asked.format("install"), "logs acme 104", asked.format("odoo")]


def test_a_build_with_neither_default_log_exits_4_and_names_both_and_the_logs_it_has(client: type[StubClient]):
    client.listed = LISTED[1:]
    client.content = {"pip": PIP, "shell": SHELL}

    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "Build 104 has no install or odoo log. It has: pip, shell.\n"
    assert client.calls[2:] == ["read acme 104 install 100", "logs acme 104"]
    assert client.streams == []


@pytest.mark.parametrize("kind", [(), ("--kind", "pip")], ids=["default", "named"])
def test_a_build_with_no_log_exits_4_and_says_so(client: type[StubClient], kind: tuple[str, ...]):
    client.listed = ()
    client.content = {}

    result = run("logs", *ON_FEATURE, *kind)

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "Build 104 has no log yet.\n"


def test_a_log_that_is_empty_prints_nothing(client: type[StubClient]):
    client.content = {"install": ()}

    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.output == ""


def test_following_a_log_that_stays_empty_prints_nothing_until_the_timeout(client: type[StubClient]):
    client.content = {"install": ()}
    client.written = ()

    result = run("logs", *ON_FEATURE, "--follow", "--timeout", "60")

    assert result.exit_code == 8
    assert result.stdout == ""


def test_a_tail_of_ten_prints_the_last_ten_lines():
    result = run("logs", *ON_FEATURE, "--tail", "10")

    assert result.exit_code == 0
    assert result.stdout == printed(*INSTALL[-10:])


def test_all_prints_the_whole_log(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "--all")

    assert result.exit_code == 0
    assert result.stdout == printed(*INSTALL)
    assert client.calls[-1] == "read acme 104 install None"


def test_a_build_can_be_named(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "--build", "103", "--tail", "1")

    assert result.exit_code == 0
    assert result.stdout == printed(INSTALL[-1])
    assert client.calls == ["branches acme", "builds feature-x", "read acme 103 install 1"]


def test_a_build_that_is_not_among_the_latest_exits_4(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "--build", "7")

    assert result.exit_code == 4
    assert result.stdout == ""
    assert "Build 7 is not among the latest builds of branch feature-x of acme." in result.stderr
    assert client.streams == []


@pytest.mark.parametrize(("kind", "lines"), [("pip", PIP), ("shell", SHELL)], ids=["known", "upstream's own"])
def test_another_kind_is_printed_by_its_name_without_listing_the_logs(
    client: type[StubClient], kind: str, lines: tuple[str, ...]
):
    result = run("logs", *ON_FEATURE, "--kind", kind)

    assert result.exit_code == 0
    assert result.stdout == printed(*lines)
    assert client.calls == ["branches acme", "latest feature-x", f"read acme 104 {kind} 100"]


@pytest.mark.parametrize("mode", [(), ("--follow",)], ids=["read", "follow"])
@pytest.mark.parametrize("kind", ["upgrade", "odoo"])
def test_a_kind_the_build_does_not_have_exits_4_and_names_the_logs_it_has(mode: tuple[str, ...], kind: str):
    result = run("logs", *ON_FEATURE, "--kind", kind, *mode)

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == f"Build 104 has no {kind} log. It has: install, pip, shell.\n"


@pytest.mark.parametrize("kind", [(), ("--kind", "pip")], ids=["default", "named"])
def test_what_else_is_not_found_keeps_its_message(
    client: type[StubClient], monkeypatch: pytest.MonkeyPatch, kind: tuple[str, ...]
):
    def read_log(*_: object, **__: object) -> Iterator[LogLine]:
        msg = "Build 104 is not among the latest builds of its branch."
        raise odouche.NotFoundError(msg)

    monkeypatch.setattr(client, "read_log", read_log)

    result = run("logs", *ON_FEATURE, *kind)

    assert result.exit_code == 4
    assert result.stderr == "Build 104 is not among the latest builds of its branch.\n"


def test_kinds_lists_the_logs_the_build_has(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "--kinds")

    assert result.exit_code == 0
    assert [line.split(maxsplit=3) for line in result.stdout.splitlines()] == [
        ["Kind", "Name", "Size", "Changed"],
        ["install", "install", "156", "KB  3 minutes ago"],
        ["pip", "pip", "2", "KB    2 hours ago"],
        ["unknown", "shell", "0", "B     1 day ago"],
    ]
    assert client.calls == ["branches acme", "latest feature-x", "logs acme 104"]


def test_kinds_as_json_is_the_log_models():
    result = run("--format", "json", "logs", *ON_FEATURE, "--kinds")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {"kind": "install", "name": "install", "modified_at": "2026-03-10T11:57:00+00:00", "size": "156 KB"},
        {"kind": "pip", "name": "pip", "modified_at": "2026-03-10T10:00:00+00:00", "size": "2 KB"},
        {"kind": "unknown", "name": "shell", "modified_at": "2026-03-09T12:00:00+00:00", "size": "0 B"},
    ]


def test_kinds_of_a_build_with_no_log_says_so_and_exits_0(client: type[StubClient]):
    client.listed = ()

    result = run("logs", *ON_FEATURE, "--kinds")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == "Build 104 has no log yet.\n"


def test_follow_prints_the_tail_then_each_new_line_flushed_on_its_own(
    client: type[StubClient], stdout: Callable[..., Stdout]
):
    sent = stdout()

    run("logs", *ON_FEATURE, "--follow", "--tail", "2")

    assert sent.flushed == ["install 119\n", "install 120\n", "written 1\n", "written 2\n"]


def test_follow_has_no_limit_and_lasts_until_interrupted(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "--follow", "--tail", "2")

    assert client.calls[-1] == "follow acme 104 install inf 2 None"
    assert result.exit_code == 130
    assert result.stdout == printed(*INSTALL[-2:], *WRITTEN)
    assert result.stderr == ""


def test_follow_reaching_its_timeout_exits_8_and_says_so(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "-f", "--timeout", "60")

    assert result.exit_code == 8
    assert result.stdout == printed(*INSTALL[-100:], *WRITTEN)
    assert result.stderr == "The log was still being followed at the timeout.\n"
    assert client.calls[-1] == "follow acme 104 install 60 100 None"
    assert client.closed == ["follow"]


def test_follow_with_all_starts_at_the_first_line(client: type[StubClient]):
    result = run("logs", *ON_FEATURE, "--follow", "--all")

    assert result.stdout == printed(*INSTALL, *WRITTEN)
    assert client.calls[-1] == "follow acme 104 install inf 0 0"


@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        (("--timeout", "60"), "--timeout goes with --follow only."),
        (("--follow", "--timeout", "0"), "--timeout"),
        (("--follow", "--timeout", "soon"), "--timeout"),
        (("--tail", "0"), "--tail"),
        (("--tail", "10", "--all"), "--all does not go with --tail."),
        (("--build", "0"), "--build"),
        (("--kinds", "--kind", "pip"), "--kinds does not go with --kind."),
        (("--kinds", "--tail", "10", "--all"), "--kinds does not go with --tail, --all."),
        (("--kinds", "--follow", "--timeout", "60"), "--kinds does not go with --follow, --timeout."),
        (("--kinds", "--strip"), "--kinds does not go with --strip or --no-strip."),
        (("--kinds", "--no-strip"), "--kinds does not go with --strip or --no-strip."),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, tuple) else "",
)
def test_options_that_do_not_go_together_are_a_usage_error_before_the_session_is_read(
    client: type[StubClient], arguments: tuple[str, ...], named: str
):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("logs", *ON_FEATURE, *arguments)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert named in " ".join(click.unstyle(result.stderr).replace("│", " ").split())


@pytest.mark.usefixtures("terminal")
def test_on_a_terminal_the_control_characters_are_stripped_and_the_tab_kept(client: type[StubClient]):
    client.content = {"install": (HOSTILE, "plain")}

    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.stdout == printed(STRIPPED, "plain")


def test_on_a_pipe_a_line_is_unchanged(client: type[StubClient]):
    client.content = {"install": (HOSTILE, "plain")}

    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.stdout == printed(HOSTILE, "plain")


@pytest.mark.usefixtures("terminal")
def test_a_followed_line_is_stripped_on_a_terminal_too(client: type[StubClient]):
    client.written = (HOSTILE,)

    result = run("logs", *ON_FEATURE, "--follow", "--tail", "1")

    assert result.stdout == printed("install 120", STRIPPED)


def test_strip_strips_on_a_pipe(client: type[StubClient]):
    client.content = {"install": (HOSTILE,)}

    assert run("logs", *ON_FEATURE, "--strip").stdout == printed(STRIPPED)


@pytest.mark.usefixtures("terminal")
def test_no_strip_leaves_a_line_unchanged_on_a_terminal(client: type[StubClient]):
    client.content = {"install": (HOSTILE,)}

    assert run("logs", *ON_FEATURE, "--no-strip").stdout == printed(HOSTILE)


def test_a_line_that_was_cut_is_printed_as_it_came(client: type[StubClient], monkeypatch: pytest.MonkeyPatch):
    def read_log(*_: object, **__: object) -> Iterator[LogLine]:
        yield LogLine(text="x" * 40, offset=70000, truncated=True)

    monkeypatch.setattr(client, "read_log", read_log)

    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 0
    assert result.stdout == printed("x" * 40)


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("forced", [(), ("--strip",)], ids=["default", "strip"])
def test_as_json_each_line_is_one_object_escaped_and_never_stripped(client: type[StubClient], forced: tuple[str, ...]):
    client.content = {"install": ("first", HOSTILE)}

    result = run("--format", "json", "logs", *ON_FEATURE, *forced)

    assert result.exit_code == 0
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"text": "first", "offset": 6, "truncated": False},
        {"text": HOSTILE, "offset": 7 + len(HOSTILE.encode()), "truncated": False},
    ]
    assert not set(result.stdout) & set("\t\r\x07\x1b\x7f\x9b")


def test_as_json_a_followed_log_is_one_object_per_line(client: type[StubClient]):
    result = run("--format", "json", "logs", *ON_FEATURE, "--follow", "--tail", "1", "--timeout", "60")

    assert result.exit_code == 8
    assert [json.loads(line)["text"] for line in result.stdout.splitlines()] == ["install 120", *WRITTEN]


@pytest.mark.parametrize("mode", [(), ("--follow",)], ids=["read", "follow"])
def test_a_closed_pipe_ends_in_silence_and_closes_the_log(
    client: type[StubClient], stdout: Callable[..., Stdout], mode: tuple[str, ...]
):
    sent = stdout(fails_at=2)

    result = run("logs", *ON_FEATURE, *mode)

    assert result.exit_code == 141
    assert result.output == ""
    assert sent.flushed == ["install 21\n", "install 22\n"]
    assert client.closed == [mode[0].removeprefix("--") if mode else "read"]


def test_an_interrupt_exits_130_in_silence_and_closes_the_log(client: type[StubClient], stdout: Callable[..., Stdout]):
    sent = stdout(fails_at=3, error=KeyboardInterrupt)

    result = run("logs", *ON_FEATURE, "--follow", "--tail", "2")

    assert result.exit_code == 130
    assert result.output == ""
    assert sent.flushed == ["install 119\n", "install 120\n", "written 1\n"]
    assert client.closed == ["follow"]


# The stub is in place before `main`, and no socket connects: the child cannot reach Odoo.sh.
CHILD = """
import socket
import sys

import odouche
import test_logs
from odouche_cli.app import main


def refuse(*_):
    raise SystemExit("The child opened a connection.")


socket.socket.connect = socket.socket.connect_ex = refuse
odouche.Client = test_logs.fresh()
odouche.Client.content = {"install": ("first", "second", "third")}
odouche.Client.pause = staticmethod(sys.stdin.readline)
sys.argv = ["osh", *sys.argv[1:]]
main()
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a closed pipe is another error there")
@pytest.mark.parametrize(
    ("arguments", "line"),
    [
        (("logs", *ON_FEATURE), b"first\n"),
        (("logs", *ON_FEATURE, "--follow"), b"first\n"),
        (("--format", "json", "logs", *ON_FEATURE), b'{"text": "first", "offset": 6, "truncated": false}\n'),
        (
            ("--format", "json", "logs", *ON_FEATURE, "--follow"),
            b'{"text": "first", "offset": 6, "truncated": false}\n',
        ),
    ],
    ids=["read", "follow", "read as json", "follow as json"],
)
def test_a_real_pipe_gets_each_line_at_once_and_closing_it_prints_nothing(arguments: tuple[str, ...], line: bytes):
    child = subprocess.Popen(  # noqa: S603 - this interpreter, on the script above
        [sys.executable, "-c", CHILD, *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)},
    )
    assert child.stdin is not None
    assert child.stdout is not None
    assert child.stderr is not None
    try:
        # The child holds the second line until its stdin closes.
        assert select.select([child.stdout], [], [], 10)[0]
        first = child.stdout.readline()
        child.stdout.close()
        _, stderr = child.communicate(timeout=10)
    finally:
        child.kill()
        child.wait()
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()

    assert first == line
    assert stderr == b""
    assert child.returncode == 141


@pytest.mark.parametrize(
    "ending",
    [TIMEOUT, RuntimeError("unexpected"), odouche.UpstreamUnavailableError("The worker cannot be reached.")],
    ids=lambda ending: type(ending).__name__,
)
def test_debug_output_holds_nothing_of_the_log(client: type[StubClient], ending: BaseException):
    client.content = {"install": (f"{MARKER} 1", f"{MARKER} 2")}
    client.written = (f"{MARKER} 3",)
    client.ending = ending

    result = run("--debug", "logs", *ON_FEATURE, "--follow")

    assert result.exit_code != 0
    assert result.stdout.count(MARKER) == 3
    assert "Traceback" in result.stderr
    assert type(ending).__name__ in result.stderr
    assert MARKER not in result.stderr


def test_a_character_stdout_cannot_encode_is_escaped_not_quoted_in_an_error(client: type[StubClient]):
    client.content = {"install": ("café → done",)}

    result = CliRunner(charset="cp1252").invoke(app, ["--debug", "logs", *ON_FEATURE])

    assert result.exit_code == 0
    assert result.stdout == "café \\u2192 done\n"
    assert "Traceback" not in result.stderr


def test_as_json_a_character_stdout_cannot_encode_is_escaped(client: type[StubClient]):
    client.content = {"install": ("café → done",)}

    result = CliRunner(charset="cp1252").invoke(app, ["--debug", "--format", "json", "logs", *ON_FEATURE])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["text"] == "café → done"
    assert "→" not in result.stdout
    assert "Traceback" not in result.stderr


def test_logged_out_exits_3_and_names_the_login(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("logs", *ON_FEATURE)

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "osh auth login" in result.stderr


def test_a_missing_branch_while_logged_out_is_a_usage_error(client: type[StubClient]):
    client.error = odouche.NoSessionError("Not logged in.")

    result = run("logs", "--project", "acme")

    assert result.exit_code == 2
    assert "No branch." in click.unstyle(result.stderr)


def test_help_opens_neither_the_keyring_nor_the_network(client: type[StubClient]):
    client.error = odouche.KeyringUnavailableError()

    result = run("logs", "--help")

    assert result.exit_code == 0
    assert "Example: osh logs --follow" in click.unstyle(result.output)


def test_the_root_lists_its_commands_by_name():
    listed = click.unstyle(run("--help").output)

    names = ["auth", "branches", "builds", "logs", "projects"]
    assert re.findall(rf"^\W*({'|'.join(names)})\s", listed, re.MULTILINE) == names
