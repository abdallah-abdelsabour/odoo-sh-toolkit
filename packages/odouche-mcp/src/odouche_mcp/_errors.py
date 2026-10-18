"""Library errors as tool errors, mapped once for every tool.

An agent gets the library's message and what to do next: never a traceback or an upstream body.
"""

import os

from mcp.server.mcpserver.exceptions import ToolError

import odouche


LOGIN = (
    "Only the user can log in: stop and ask them to run `osh auth login` in a terminal, then call the "
    f"tool again. Where there is no keyring, they restart this server with {odouche.SESSION_ENV} set."
)

# `osh auth login` changes nothing for a server that has the variable: it is read first.
ENVIRONMENT = (
    f"The session comes from {odouche.SESSION_ENV}. Only the user can log in: stop and ask them to restart this "
    "server with a current value, or without the variable to use the session `osh auth login` stores."
)

# The first row an error is an instance of wins, so the base class comes last.
NEXT_STEPS: tuple[tuple[type[odouche.OdoucheError], str | None], ...] = (
    (odouche.NoSessionError, LOGIN),
    (odouche.SessionExpiredError, LOGIN),
    (odouche.NotFoundError, None),
    (odouche.PermissionDeniedError, None),
    (odouche.UpstreamChangedError, None),
    (odouche.UpstreamUnavailableError, "Try again later."),
    (odouche.StreamTimeoutError, None),
    (odouche.LoginTimeoutError, None),
    (odouche.ReadOnlyError, None),
    (odouche.StageRefusedError, None),
    (odouche.FirewallRefusedError, None),
    (odouche.OutcomeUnknownError, "List the branch's builds to see whether it was carried out before trying again."),
    (odouche.KeyringUnavailableError, LOGIN),
    (odouche.LoginError, None),
    (odouche.OdoucheError, None),
)


# Where to look after an unconfirmed change, by its operation. Others look at the branch's builds.
LOOK = {"firewall change": "Read the firewall with get_firewall to see whether it was carried out before trying again."}


def login_step() -> str:
    """Return what the user does to give the server a session, by where it reads one from."""
    return ENVIRONMENT if os.environ.get(odouche.SESSION_ENV) else LOGIN


def tool_error(error: odouche.OdoucheError) -> ToolError:
    """Return the tool error that says what happened and, when there is one, the next step."""
    step = next(step for kind, step in NEXT_STEPS if isinstance(error, kind))
    if step is LOGIN:
        step = login_step()
    elif isinstance(error, odouche.OutcomeUnknownError):
        step = LOOK.get(error.operation or "", step)
    return ToolError(f"{error} {step}" if step else str(error))
