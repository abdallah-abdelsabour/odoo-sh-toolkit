import os
import sys

import click
import pytest
import typer
from typer.testing import CliRunner, Result

import odouche
from odouche_cli import _errors
from odouche_cli._errors import (
    EXIT_BUILD_FAILED,
    EXIT_BUILD_NO_RESULT,
    EXIT_BUILD_TIMEOUT,
    EXIT_BUILD_WARNING,
    EXIT_CODES,
    ISSUES_URL,
    DebugOption,
    OshGroup,
)
from odouche_cli.app import app


SENTINEL = "sentinel-session-value"

CASES: list[tuple[odouche.OdoucheError, int]] = [
    (odouche.NoSessionError("No session."), 3),
    (odouche.SessionExpiredError("The session has expired."), 3),
    (odouche.NotFoundError("No such project."), 4),
    (odouche.PermissionDeniedError("Not allowed."), 5),
    (odouche.UpstreamChangedError("projects", "name"), 6),
    (odouche.UpstreamUnavailableError("Odoo.sh cannot be reached."), 7),
    (odouche.StreamTimeoutError("Still running."), 8),
    (odouche.LoginTimeoutError("Nobody logged in."), 8),
    (odouche.ReadOnlyError("Read-only."), 9),
    (odouche.StageRefusedError("A production branch."), 9),
    (odouche.OutcomeUnknownError("Not confirmed."), 10),
    (odouche.KeyringUnavailableError(), 11),
    (odouche.LoginError("No session captured."), 12),
    (odouche.OdoucheError("Something else."), 13),
]

runner = CliRunner()


def run(error: BaseException, *args: str) -> Result:
    """Run a stub command that holds a session and raises `error`."""
    stub = typer.Typer(cls=OshGroup)

    @stub.callback()
    def root(*, debug: DebugOption = False) -> None:  # pyright: ignore[reportUnusedFunction]
        pass

    @stub.command()
    def fail() -> None:  # pyright: ignore[reportUnusedFunction]
        session = odouche.Secret(SENTINEL)
        assert session.expose_secret() == SENTINEL
        raise error

    return runner.invoke(stub, [*args, "fail"])


@pytest.mark.parametrize(("error", "code"), CASES, ids=lambda value: type(value).__name__)
def test_a_library_error_is_a_message_and_an_exit_code(error: odouche.OdoucheError, code: int):
    result = run(error)

    assert result.exit_code == code
    assert result.stdout == ""
    assert str(error) in click.unstyle(result.stderr)
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(("error", "code"), CASES, ids=lambda value: type(value).__name__)
def test_debug_adds_the_traceback_and_keeps_the_exit_code(error: odouche.OdoucheError, code: int):
    result = run(error, "--debug")

    assert result.exit_code == code
    assert result.stdout == ""
    assert "Traceback" in result.stderr


def test_debug_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OSH_DEBUG", "1")

    assert "Traceback" in run(odouche.NotFoundError("No such project.")).stderr


def test_a_message_has_no_control_character():
    result = run(odouche.NotFoundError("Project \x1b[2Jacme\x9b31m has no branch a\nb."))

    assert result.exit_code == 4
    assert result.stderr == "Project [2Jacme31m has no branch a b.\n"


def test_no_session_names_the_login_command():
    result = run(odouche.NoSessionError("No session."))

    assert "osh auth login" in click.unstyle(result.stderr)


@pytest.mark.parametrize("args", [(), ("--debug",)])
def test_the_session_is_in_neither_stream(args: tuple[str, ...]):
    result = run(odouche.SessionExpiredError("The session has expired."), *args)

    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr


def test_an_unexpected_error_asks_for_a_report_and_hides_its_text():
    result = run(ValueError("the detail"))

    stderr = click.unstyle(result.stderr)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert ISSUES_URL in stderr
    assert "ValueError" in stderr
    assert "--debug" in stderr
    assert "the detail" not in stderr
    assert "Traceback" not in stderr


def test_an_unexpected_error_shows_its_traceback_under_debug():
    result = run(ValueError("the detail"), "--debug")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Traceback" in result.stderr
    assert "the detail" in result.stderr


def test_the_session_of_the_environment_is_masked_in_the_traceback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(odouche.SESSION_ENV, SENTINEL)

    result = run(ValueError(f"sent {SENTINEL}"), "--debug")

    assert result.exit_code == 1
    assert "Traceback" in result.stderr
    assert f"sent {odouche.Secret(SENTINEL)}" in result.stderr
    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr


def test_the_traceback_has_no_control_character_and_keeps_its_lines():
    result = run(odouche.NotFoundError("Project \x07acme\x9b31m has no branch a\rb."), "--debug")

    lines = result.stderr.split("\n")
    assert result.exit_code == 4
    assert lines[0] == "Traceback (most recent call last):"
    assert lines[-3].endswith("NotFoundError: Project acme31m has no branch a b.")
    assert lines[-2:] == ["Project acme31m has no branch a b.", ""]


def test_a_control_character_does_not_hide_the_session_in_the_traceback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(odouche.SESSION_ENV, SENTINEL)

    result = run(ValueError(f"sent {SENTINEL[:4]}\x07{SENTINEL[4:]}"), "--debug")

    assert SENTINEL not in result.stderr


def test_an_empty_session_in_the_environment_leaves_the_traceback_whole(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(odouche.SESSION_ENV, "")

    result = run(ValueError("the detail"), "--debug")

    assert "ValueError: the detail" in result.stderr
    assert str(odouche.Secret("")) not in result.stderr


def test_an_interrupt_exits_130_in_silence():
    result = run(KeyboardInterrupt())

    assert result.exit_code == 130
    assert result.output == ""


def test_a_closed_pipe_ends_in_silence_as_sigpipe_would():
    result = run(BrokenPipeError())

    assert result.exit_code == 141
    assert result.stderr == ""


@pytest.mark.skipif(sys.platform == "win32", reason="a closed pipe is another error there")
def test_after_a_closed_pipe_what_stdout_still_holds_is_flushed_without_an_error(monkeypatch: pytest.MonkeyPatch):
    reader, writer = os.pipe()
    os.close(reader)
    with os.fdopen(writer, "w") as stdout:
        monkeypatch.setattr(sys, "stdout", stdout)
        stdout.write("held")

        _errors._discard_stdout()

        stdout.flush()


@pytest.mark.parametrize("error", [odouche.LoginError("No session captured."), odouche.LoginTimeoutError("Late.")])
def test_a_failed_login_says_to_log_in_again(error: odouche.OdoucheError):
    assert "osh auth login` again" in click.unstyle(run(error).stderr)


def test_an_exit_keeps_its_code():
    assert run(typer.Exit(42)).exit_code == 42


def test_a_usage_error_still_exits_2():
    stub = typer.Typer(cls=OshGroup)
    stub.command()(lambda: None)

    assert runner.invoke(stub, ["--bogus"]).exit_code == 2
    assert runner.invoke(app, ["--bogus"]).exit_code == 2


def test_every_library_error_has_its_own_row():
    exported = {
        value
        for name in odouche.__all__
        if isinstance(value := getattr(odouche, name), type) and issubclass(value, odouche.OdoucheError)
    }

    assert {kind for kind, _, _ in EXIT_CODES} == exported
    assert EXIT_CODES[-1][0] is odouche.OdoucheError


def test_no_code_collides_with_the_reserved_ones():
    assert all(2 < code < 20 for _, code, _ in EXIT_CODES)


def test_the_codes_of_a_watched_build_are_distinct_and_in_their_range():
    codes = [EXIT_BUILD_FAILED, EXIT_BUILD_WARNING, EXIT_BUILD_NO_RESULT, EXIT_BUILD_TIMEOUT]

    assert len(set(codes)) == len(codes)
    assert all(20 <= code < 30 for code in codes)


def test_the_application_never_shows_locals():
    assert app.pretty_exceptions_show_locals is False
    assert app.info.cls is OshGroup
