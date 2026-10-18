"""Logging out: the session ended on Odoo.sh, and the stored one deleted."""

import logging
from collections.abc import Callable

from odouche._session import SessionStore, UnreadableError
from odouche._upstream import user
from odouche._upstream.transport import Transport
from odouche.errors import KeyringUnavailableError, NoSessionError, OdoucheError, SessionExpiredError
from odouche.models import LogoutResult, SessionSource
from odouche.secret import Secret


_logger = logging.getLogger("odouche")

# What the tests replace: the connection to Odoo.sh.
_transport: Callable[[Secret], Transport] = Transport


def logout(session: Secret | None = None, *, invalidate_given: bool = False) -> LogoutResult:
    """Log out of Odoo.sh: end the stored session there, and delete it from the keyring.

    The stored session is deleted even when Odoo.sh cannot be asked to end it. The result's
    `failure` then says why, and a copy of the session works until Odoo.sh expires it. With no
    session nothing is raised. A stored one past its max age, or that cannot be read, is deleted
    and not ended.

    A session passed or set in the environment is not the library's to delete: the result names
    its source, and unsetting it is left to the caller. It is ended on Odoo.sh only with
    `invalidate_given`, since others may be using it. The stored session is then left as it is.

    Raises `NoSessionError` when the session passed or set in the environment is not a cookie
    value, and `KeyringUnavailableError` when the keyring cannot be read or the session not
    deleted. When it is not deleted, the message says whether Odoo.sh ended it.
    """
    store = SessionStore(session)
    given = store.given()
    try:
        resolved = given or store.load()
    except (UnreadableError, SessionExpiredError):
        return LogoutResult(source=SessionSource.KEYRING, deleted=True, invalidated=False, failure=None)
    except NoSessionError:
        return LogoutResult(source=None, deleted=False, invalidated=False, failure=None)
    stored = resolved.source is SessionSource.KEYRING
    invalidated, failure = False, None
    try:
        if stored or invalidate_given:
            failure = _end(resolved.session)
            invalidated = failure is None
    except BaseException:
        # An unreachable Odoo.sh is waited for long enough to be interrupted.
        store.discard()
        raise
    if stored:
        try:
            store.delete()
        except KeyringUnavailableError as error:
            ended = "was" if invalidated else "was not"
            raise KeyringUnavailableError(f"{error} The session {ended} ended on Odoo.sh.") from None
    _logger.debug("logout: session %s", "deleted" if stored else "left where it is")
    return LogoutResult(source=resolved.source, deleted=stored, invalidated=invalidated, failure=failure)


def _end(session: Secret) -> OdoucheError | None:
    """Ask Odoo.sh to end the session, and return why it could not be asked."""
    with _transport(session) as transport:
        try:
            user.logout(transport)
        except SessionExpiredError:
            return None
        except OdoucheError as error:
            return error
    return None
