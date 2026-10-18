"""Library errors as exit codes and messages, handled once at the root of `osh`.

Scripts branch on the codes, so changing one is a breaking change. `docs/cli.md` has the table.
"""

import os
import sys
import traceback
from contextlib import suppress
from typing import Annotated, Any, override

import typer
from typer.core import TyperGroup

import odouche
from odouche_cli._output import strip_control


ISSUES_URL = "https://github.com/avanserv/odouche/issues"

EXIT_UNEXPECTED = 1
# What click exits with when a prompt is aborted.
EXIT_DECLINED = 1

_LOGIN = "Run `osh auth login`."
_LOGIN_AGAIN = "Run `osh auth login` again."

# What `osh builds watch` exits with, in the 20 to 29 kept for the result of a build.
EXIT_BUILD_FAILED = 20
EXIT_BUILD_WARNING = 21
EXIT_BUILD_NO_RESULT = 22
EXIT_BUILD_TIMEOUT = 23

# What `osh ssh` exits with when there is no `ssh` to become.
EXIT_NO_SSH = 14

# What `osh firewall allow` exits with when the public address is not found and none was given.
EXIT_NO_ADDRESS = 15

# What a shell reports for a command that SIGPIPE ended.
EXIT_CLOSED_PIPE = 141

# The first row an error is an instance of wins, so the base class comes last. 1 and 2 are the
# unexpected error and click's usage error.
EXIT_CODES: tuple[tuple[type[odouche.OdoucheError], int, str | None], ...] = (
    (odouche.NoSessionError, 3, _LOGIN),
    (odouche.SessionExpiredError, 3, _LOGIN),
    (odouche.NotFoundError, 4, None),
    (odouche.PermissionDeniedError, 5, None),
    (odouche.UpstreamChangedError, 6, None),
    (odouche.UpstreamUnavailableError, 7, "Try again later."),
    (odouche.StreamTimeoutError, 8, None),
    (odouche.LoginTimeoutError, 8, _LOGIN_AGAIN),
    (odouche.ReadOnlyError, 9, None),
    (odouche.StageRefusedError, 9, None),
    (odouche.FirewallRefusedError, 9, None),
    (odouche.OutcomeUnknownError, 10, "`osh builds list` shows whether the build was started."),
    (odouche.KeyringUnavailableError, 11, None),
    (odouche.LoginError, 12, _LOGIN_AGAIN),
    (odouche.OdoucheError, 13, None),
)

# Where to look after an unconfirmed change, by its operation. Others look at the builds.
_LOOK = {"firewall change": "`osh firewall list` shows the addresses Odoo.sh has."}

DebugOption = Annotated[
    bool,
    typer.Option("--debug", envvar="OSH_DEBUG", help="Show the traceback when a command fails."),
]


class OshGroup(TyperGroup):
    """The root group: a failed command ends with a message on stderr and an exit code."""

    @override
    def list_commands(self, ctx: typer.Context) -> list[str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """List the commands by name: typer lists a command before every group."""
        return sorted(super().list_commands(ctx))

    # The base names typer's vendored click context, which is not public. Typer passes its own.
    @override
    def invoke(self, ctx: typer.Context) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            return super().invoke(ctx)
        except (typer.Exit, typer.Abort, typer.TyperException):
            raise
        # A pipe closed under a command's output, as by `| head`, ends the command quietly.
        except BrokenPipeError:
            _discard_stdout()
            ctx.exit(EXIT_CLOSED_PIPE)
        except Exception as error:  # noqa: BLE001 - the root reports every failure
            debug = bool(ctx.params.get("debug"))
            if debug:
                _print_traceback(error)
            ctx.exit(_report(error, debug=debug))


def _discard_stdout() -> None:
    """Point stdout at the null device, so that the flush at exit does not fail on the closed pipe."""
    # A stdout that is not a file has nothing to flush there.
    with suppress(OSError, ValueError):
        descriptor = sys.stdout.fileno()
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, descriptor)
        os.close(null)


def _print_traceback(error: Exception) -> None:
    """Print the standard traceback, which has no locals, with the environment's session masked.

    Each line loses its control characters first, so that one inside the session cannot hide it.
    """
    text = "".join(traceback.format_exception(error))
    text = "\n".join(strip_control(line) for line in text.split("\n"))
    if session := os.environ.get(odouche.SESSION_ENV):
        text = text.replace(session, str(odouche.Secret(session)))
    typer.echo(text, err=True, nl=False)


def _report(error: Exception, *, debug: bool) -> int:
    """Print what happened and what to do on stderr, and return the exit code."""
    if not isinstance(error, odouche.OdoucheError):
        typer.echo(f"Unexpected error ({type(error).__name__}). Please report it at {ISSUES_URL}", err=True)
        if not debug:
            typer.echo("Run again with --debug for the traceback.", err=True)
        return EXIT_UNEXPECTED
    code, hint = next((code, hint) for kind, code, hint in EXIT_CODES if isinstance(error, kind))
    if isinstance(error, odouche.OutcomeUnknownError):
        hint = _LOOK.get(error.operation or "", hint)
    typer.echo(strip_control(str(error)), err=True)
    if hint:
        typer.echo(hint, err=True)
    return code
