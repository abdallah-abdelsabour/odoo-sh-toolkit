"""`osh auth`: log in, log out, and say what session is in use."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import typer

import odouche
from odouche_cli._client import open_client
from odouche_cli._output import Column, Output
from odouche_cli._time import ago, left


app = typer.Typer(
    name="auth",
    help="Log in to Odoo.sh, log out, and show the session in use.",
    epilog="Example: osh auth login",
    no_args_is_help=True,
)

_STEPS = {
    odouche.LoginStep.KEYRING: "Checking the keyring. Unlock it if it asks.",
    odouche.LoginStep.BROWSER: "A browser window is open: sign in to Odoo.sh with GitHub there.",
    odouche.LoginStep.PASTE: (
        "No browser can be launched here. Sign in to Odoo.sh in your own browser, "
        "then paste the session_id cookie of www.odoo.sh."
    ),
}

_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Login:
    """What `osh auth login` did."""

    identity: odouche.Identity | None
    """Who the stored session belongs to, or `None` when the environment's is the one in use."""


@dataclass(frozen=True, slots=True)
class Logout:
    """What `osh auth logout` did: the library's result, with the failure as its message."""

    source: odouche.SessionSource | None
    deleted: bool
    invalidated: bool
    failure: str | None


@app.command(epilog="Example: osh auth login")
def login(ctx: typer.Context) -> None:
    """Sign in with GitHub in a browser and store the session in the keyring.

    Where no browser can be launched, it asks for the `session_id` cookie in a prompt that does not echo it.
    """
    output: Output = ctx.obj
    odouche.login(ask=_ask, notify=lambda step: typer.echo(_STEPS[step], err=True))
    typer.echo("The session is stored in the keyring.", err=True)
    if os.environ.get(odouche.SESSION_ENV):
        typer.echo(f"{odouche.SESSION_ENV} is set and is used instead of it until it is unset.", err=True)
        identity = None
    else:
        with open_client() as client:
            identity = client.identity()
    output.stream([Login(identity)], _login_line)


@app.command(epilog="Example: osh auth status --check")
def status(
    ctx: typer.Context,
    *,
    check: Annotated[
        bool,
        typer.Option("--check", help="Ask Odoo.sh whether it still accepts the session, and who it belongs to."),
    ] = False,
) -> None:
    """Show where the session comes from and how long it lasts, without asking Odoo.sh. Exits 3 when there is none."""
    output: Output = ctx.obj
    with open_client() as client:
        if not check:
            output.one(client.session, _session_columns(_itself))
            return
        identity = client.identity()
    output.one(
        identity,
        [
            Column[odouche.Identity]("User", lambda found: found.username),
            Column[odouche.Identity]("Name", lambda found: found.name),
            Column[odouche.Identity]("Email", lambda found: found.email),
            *_session_columns(_session_of),
        ],
    )


@app.command(epilog="Example: osh auth logout")
def logout(ctx: typer.Context) -> None:
    """End the stored session on Odoo.sh and remove it from the keyring. Exits 0 when there is none."""
    output: Output = ctx.obj
    result = odouche.logout()
    failure = None if result.failure is None else str(result.failure)
    output.stream([Logout(result.source, result.deleted, result.invalidated, failure)], _logout_line)


def _ask() -> odouche.Secret:
    return odouche.Secret(typer.prompt("session_id", hide_input=True, err=True))


def _itself(info: odouche.SessionInfo) -> odouche.SessionInfo:
    return info


def _session_of(identity: odouche.Identity) -> odouche.SessionInfo:
    return identity.session


def _session_columns[T](session: Callable[[T], odouche.SessionInfo]) -> list[Column[T]]:
    return [
        Column[T]("Source", lambda item: session(item).source),
        Column[T]("Stored", lambda item: _ago(session(item).stored_at)),
        Column[T]("Expires", lambda item: _left(session(item).expires_at)),
    ]


def _ago(moment: datetime | None) -> str:
    return _UNKNOWN if moment is None else ago(moment)


def _left(moment: datetime | None) -> str:
    return _UNKNOWN if moment is None else left(moment)


def _login_line(result: Login) -> str:
    return "Logged in." if result.identity is None else f"Logged in as {result.identity.username}."


def _logout_line(result: Logout) -> str:
    if result.source is None:
        return "Not logged in."
    if not result.deleted:
        return (
            f"The session comes from {odouche.SESSION_ENV}, which osh cannot unset. "
            "Nothing was changed: unset it yourself."
        )
    removed = "The stored session was removed from the keyring"
    if result.invalidated:
        return f"{removed} and ended on Odoo.sh."
    if result.failure is None:
        return f"{removed}. It was not ended on Odoo.sh."
    return f"{removed}. Odoo.sh could not be asked to end it, so a copy of it works until it expires: {result.failure}"
