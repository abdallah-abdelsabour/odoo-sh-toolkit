"""Where every tool gets its session, and the tool that says whether there is one.

No tool takes a session: arguments are visible to the model and logged by clients.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import odouche
from odouche_mcp._errors import login_step


def open_client(*, changes: bool = False) -> odouche.Client:
    """Return a client on the session in the environment, then the stored one, read-only unless `changes`.

    A tool opens one per call, so a login made while the server runs is picked up.
    """
    return odouche.Client(read_only=not changes)


@dataclass(frozen=True)
class SessionStatus:
    available: bool
    identity: odouche.Identity | None
    seconds_left: int | None
    problem: str | None


def get_session() -> SessionStatus:
    """Report whether the server has an Odoo.sh session, where it came from and who it belongs to.

    The server cannot log in, and neither can an agent: with no session, `problem` says what the
    user has to do.
    """
    try:
        client = open_client()
    except (odouche.NoSessionError, odouche.SessionExpiredError, odouche.KeyringUnavailableError) as error:
        return SessionStatus(available=False, identity=None, seconds_left=None, problem=f"{error} {login_step()}")
    with client:
        identity = client.identity()
    expires_at = identity.session.expires_at
    left = None if expires_at is None else max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    return SessionStatus(available=True, identity=identity, seconds_left=left, problem=None)
