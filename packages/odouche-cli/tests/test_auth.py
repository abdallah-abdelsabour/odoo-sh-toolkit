import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import click
import pytest
import typer
from typer.testing import CliRunner, Result

import odouche
from odouche_cli import _time, auth
from odouche_cli.app import app


SENTINEL = "sentinel-session-value"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
STORED = odouche.SessionInfo(odouche.SessionSource.KEYRING, NOW - timedelta(days=3), NOW + timedelta(days=27))
GIVEN = odouche.SessionInfo(odouche.SessionSource.ENVIRONMENT, None, None)

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: holds a session and answers or raises."""

    info: odouche.SessionInfo = STORED
    error: odouche.OdoucheError | None = None
    modes: list[bool]
    """The `read_only` each client was built with."""
    rejected: odouche.OdoucheError | None = None
    asked: int = 0

    def __init__(self, *, read_only: bool = False) -> None:
        self.modes.append(read_only)
        self._held = odouche.Secret(SENTINEL)
        if self.error is not None:
            raise self.error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    @property
    def session(self) -> odouche.SessionInfo:
        return self.info

    def identity(self) -> odouche.Identity:
        type(self).asked += 1
        if self.rejected is not None:
            raise self.rejected
        return odouche.Identity(
            user_id=7, name="Ada Lovelace", username="ada", email="ada@example.com", session=self.info
        )


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []

    monkeypatch.delenv(odouche.SESSION_ENV, raising=False)
    monkeypatch.setattr(odouche, "Client", Client)
    monkeypatch.setattr(_time, "_now", lambda: NOW)
    return Client


def run(*args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=stdin)


def log_in(monkeypatch: pytest.MonkeyPatch, *steps: odouche.LoginStep, error: odouche.OdoucheError | None = None):
    """Replace `odouche.login` with one that goes through `steps`, and return what it was asked for."""
    pasted: list[odouche.Secret] = []

    def login(*, ask: Callable[[], odouche.Secret], notify: Callable[[odouche.LoginStep], None]) -> None:
        for step in steps:
            notify(step)
            if step is odouche.LoginStep.PASTE:
                pasted.append(ask())
        if error is not None:
            raise error

    monkeypatch.setattr(odouche, "login", login)
    return pasted


def log_out(monkeypatch: pytest.MonkeyPatch, result: odouche.LogoutResult) -> None:
    monkeypatch.setattr(odouche, "logout", lambda: result)


def test_login_names_the_user(monkeypatch: pytest.MonkeyPatch):
    log_in(monkeypatch, odouche.LoginStep.KEYRING, odouche.LoginStep.BROWSER)

    result = run("auth", "login")

    assert result.exit_code == 0
    assert result.stdout == "Logged in as ada.\n"
    assert "keyring" in result.stderr
    assert "browser" in result.stderr


def test_login_says_the_session_is_stored_before_asking_who_it_belongs_to(
    monkeypatch: pytest.MonkeyPatch, client: type[StubClient]
):
    log_in(monkeypatch)
    client.rejected = odouche.UpstreamUnavailableError("Odoo.sh cannot be reached.")

    result = run("auth", "login")

    assert result.exit_code == 7
    assert "The session is stored in the keyring." in result.stderr


def test_login_as_json_is_the_identity(monkeypatch: pytest.MonkeyPatch):
    log_in(monkeypatch)

    result = run("--format", "json", "auth", "login")

    assert json.loads(result.stdout)["identity"]["username"] == "ada"


def test_login_asks_for_a_pasted_session_without_echo(monkeypatch: pytest.MonkeyPatch):
    pasted = log_in(monkeypatch, odouche.LoginStep.PASTE)

    result = run("auth", "login", stdin=f"{SENTINEL}\n")

    assert result.exit_code == 0
    assert [secret.expose_secret() for secret in pasted] == [SENTINEL]
    assert "session_id" in result.stderr
    assert SENTINEL not in result.output


def test_a_failed_login_is_its_exit_code(monkeypatch: pytest.MonkeyPatch, client: type[StubClient]):
    log_in(monkeypatch, error=odouche.LoginError("No session captured."))

    result = run("auth", "login")

    assert result.exit_code == 12
    assert result.stdout == ""
    assert client.asked == 0


def test_login_does_not_name_the_user_of_the_environment_session(
    monkeypatch: pytest.MonkeyPatch, client: type[StubClient]
):
    log_in(monkeypatch)
    monkeypatch.setenv(odouche.SESSION_ENV, SENTINEL)

    result = run("--format", "json", "auth", "login")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"identity": None}
    assert odouche.SESSION_ENV in result.stderr
    assert client.asked == 0


def test_status_shows_the_source_the_age_and_the_time_left(client: type[StubClient]):
    result = run("auth", "status")

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["Source   keyring", "Stored   3 days ago", "Expires  in 27 days"]
    assert client.asked == 0


def test_status_of_an_environment_session_has_no_age(client: type[StubClient]):
    client.info = GIVEN

    result = run("auth", "status")

    assert result.stdout.splitlines() == ["Source   environment", "Stored   unknown", "Expires  unknown"]


def test_status_as_json_is_the_session_info():
    result = run("--format", "json", "auth", "status")

    assert json.loads(result.stdout) == {
        "source": "keyring",
        "stored_at": "2026-10-01T12:00:00+00:00",
        "expires_at": "2026-10-31T12:00:00+00:00",
    }


@pytest.mark.parametrize("error", [odouche.NoSessionError("Not logged in."), odouche.SessionExpiredError("Too old.")])
def test_status_without_a_usable_session_exits_3(client: type[StubClient], error: odouche.OdoucheError):
    client.error = error

    result = run("auth", "status")

    assert result.exit_code == 3
    assert result.stdout == ""


def test_status_check_asks_odoo_sh_who_the_session_belongs_to(client: type[StubClient]):
    result = run("auth", "status", "--check")

    assert result.exit_code == 0
    assert client.asked == 1
    assert result.stdout.splitlines()[:3] == ["User     ada", "Name     Ada Lovelace", "Email    ada@example.com"]
    assert "Source   keyring" in result.stdout


def test_status_check_as_json_is_the_identity():
    result = run("--format", "json", "auth", "status", "--check")

    found = json.loads(result.stdout)
    assert found["username"] == "ada"
    assert found["session"]["source"] == "keyring"


def test_status_check_of_a_rejected_session_exits_3(client: type[StubClient]):
    client.rejected = odouche.SessionExpiredError("Odoo.sh no longer accepts the session.")

    result = run("auth", "status", "--check")

    assert result.exit_code == 3
    assert result.stdout == ""


LOGOUTS: list[tuple[odouche.LogoutResult, list[str]]] = [
    (odouche.LogoutResult(None, deleted=False, invalidated=False, failure=None), ["Not logged in."]),
    (
        odouche.LogoutResult(odouche.SessionSource.KEYRING, deleted=True, invalidated=True, failure=None),
        ["removed from the keyring", "and ended on Odoo.sh"],
    ),
    (
        odouche.LogoutResult(odouche.SessionSource.KEYRING, deleted=True, invalidated=False, failure=None),
        ["removed from the keyring", "was not ended on Odoo.sh"],
    ),
    (
        odouche.LogoutResult(
            odouche.SessionSource.KEYRING,
            deleted=True,
            invalidated=False,
            failure=odouche.UpstreamUnavailableError("Odoo.sh cannot be reached."),
        ),
        ["removed from the keyring", "could not be asked", "Odoo.sh cannot be reached."],
    ),
    (
        odouche.LogoutResult(odouche.SessionSource.ENVIRONMENT, deleted=False, invalidated=False, failure=None),
        [odouche.SESSION_ENV, "cannot unset"],
    ),
]
LOGOUT_IDS = ["none", "ended", "not-ended", "unreachable", "environment"]


@pytest.mark.parametrize(("outcome", "said"), LOGOUTS, ids=LOGOUT_IDS)
def test_logout_says_what_it_did(monkeypatch: pytest.MonkeyPatch, outcome: odouche.LogoutResult, said: list[str]):
    log_out(monkeypatch, outcome)

    result = run("auth", "logout")

    assert result.exit_code == 0
    for text in said:
        assert text in result.stdout


def test_logout_as_json_has_the_failure_as_its_message(monkeypatch: pytest.MonkeyPatch):
    log_out(monkeypatch, LOGOUTS[3][0])

    result = run("--format", "json", "auth", "logout")

    assert json.loads(result.stdout) == {
        "source": "keyring",
        "deleted": True,
        "invalidated": False,
        "failure": "Odoo.sh cannot be reached.",
    }


@pytest.mark.parametrize("output_format", ["table", "json"])
@pytest.mark.parametrize("command", [("login",), ("status",), ("status", "--check"), ("logout",)])
@pytest.mark.parametrize("source", [odouche.SessionSource.KEYRING, odouche.SessionSource.ENVIRONMENT])
def test_the_session_is_in_neither_stream(
    monkeypatch: pytest.MonkeyPatch, output_format: str, command: tuple[str, ...], source: odouche.SessionSource
):
    log_in(monkeypatch, odouche.LoginStep.PASTE)
    given = source is odouche.SessionSource.ENVIRONMENT
    log_out(monkeypatch, LOGOUTS[4][0] if given else LOGOUTS[1][0])
    if given:
        monkeypatch.setenv(odouche.SESSION_ENV, SENTINEL)

    result = run("--format", output_format, "--debug", "auth", *command, stdin=f"{SENTINEL}\n")

    assert result.exit_code == 0
    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr


def test_no_option_takes_a_session():
    group: Any = typer.main.get_command(auth.app)

    names = [name for command in group.commands.values() for param in command.params for name in param.opts]

    assert set(group.commands) == {"login", "status", "logout"}
    assert "--check" in names
    assert not [name for name in names if "session" in name or "cookie" in name]


def test_help_opens_neither_the_keyring_nor_the_network(client: type[StubClient]):
    client.error = odouche.KeyringUnavailableError()

    result = run("auth", "--help")

    assert result.exit_code == 0
    assert "login" in click.unstyle(result.output)
