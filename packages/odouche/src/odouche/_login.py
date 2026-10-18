"""Logging in: a session captured from a browser or pasted, verified, then stored."""

import logging
import time
from collections.abc import Callable
from enum import Enum

from odouche._session import SessionStore
from odouche._upstream.browser import Capture, find
from odouche._upstream.projects import PATH as _PROJECTS
from odouche._upstream.transport import Transport, is_sendable
from odouche.errors import LoginError, LoginTimeoutError, SessionExpiredError
from odouche.secret import Secret


_POLL = 1.0

_logger = logging.getLogger("odouche")

# What the tests replace: the browser, the connection to Odoo.sh and the time.
_find = find
_capture = Capture
_transport: Callable[[Secret], Transport] = Transport
_monotonic = time.monotonic
_sleep = time.sleep


class LoginStep(Enum):
    """What a login is waiting for, for a frontend to say in its own words."""

    KEYRING = "keyring"
    """The keyring is being checked, and may be showing a dialog to be unlocked."""

    BROWSER = "browser"
    """A browser window is open on the Odoo.sh login, for the user to sign in with GitHub."""

    PASTE = "paste"
    """No browser can be launched here: the `session_id` cookie is about to be asked for."""


def login(
    *,
    ask: Callable[[], Secret] | None = None,
    notify: Callable[[LoginStep], None] = lambda _: None,
    timeout: float = 300,
) -> None:
    """Log in to Odoo.sh and store the session in the keyring, replacing the stored one.

    The user signs in with GitHub in a browser launched for the login, with a profile that is
    deleted when it ends. Where no browser can be launched, `ask` is called for the `session_id`
    cookie of `www.odoo.sh`: prompt for it without echo and return it wrapped in a `Secret`.
    `notify` is called with each `LoginStep`. The function itself neither prints nor prompts.

    The session is stored once Odoo.sh has answered one request sent with it. Raises
    `LoginTimeoutError` when the sign-in has been waited for `timeout` seconds,
    `LoginError` when no session is obtained or Odoo.sh refuses the one pasted, and
    `KeyringUnavailableError`, before anything is asked of the user, when there is nowhere to
    store one or the keyring has not been unlocked within `timeout` seconds.
    """
    store = SessionStore()
    notify(LoginStep.KEYRING)
    store.check(timeout)
    browser = _find()
    session = _pasted(ask, notify) if browser is None else _captured(browser, notify, timeout)
    store.save(session, timeout)
    _logger.debug("login: session stored")


def _captured(browser: str, notify: Callable[[LoginStep], None], timeout: float) -> Secret:
    deadline = _monotonic() + timeout
    seen: Secret | None = None
    with _capture(browser) as capture:
        _logger.debug("login: browser launched")
        notify(LoginStep.BROWSER)
        while _monotonic() < deadline:
            candidate = capture.cookie()
            # Odoo.sh sets an anonymous session before the sign-in, and replaces it at the end.
            if candidate is not None and candidate != seen:
                seen = candidate
                if _accepted(candidate):
                    return candidate
            _sleep(_POLL)
    raise LoginTimeoutError(f"The login was not completed within {timeout:.0f} seconds.")


def _pasted(ask: Callable[[], Secret] | None, notify: Callable[[LoginStep], None]) -> Secret:
    if ask is None:
        raise LoginError("No browser can be launched here, and there is no prompt to paste the session into.")
    notify(LoginStep.PASTE)
    session = ask()
    if not is_sendable(session):
        raise LoginError("The pasted value is not a session_id cookie value.")
    if not _accepted(session):
        raise LoginError("Odoo.sh did not accept the pasted session.")
    return session


def _accepted(session: Secret) -> bool:
    """Send the one request that tells whether Odoo.sh answers this session."""
    if not is_sendable(session):
        return False
    with _transport(session) as transport:
        try:
            transport.call("login", _PROJECTS, retry=True)
        except SessionExpiredError:
            accepted = False
        else:
            accepted = True
    _logger.debug("login: session %s", "accepted" if accepted else "not accepted")
    return accepted
