"""Where a session lives: the OS keyring between runs, or the environment in memory only."""

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, cast

from odouche._upstream.transport import is_sendable
from odouche.errors import KeyringUnavailableError, NoSessionError, SessionExpiredError
from odouche.models import SessionSource
from odouche.secret import Secret


if TYPE_CHECKING:
    from keyring.backend import KeyringBackend


SESSION_ENV = "ODOUCHE_SESSION"
"""The environment variable a session is read from. It wins over the keyring and is never stored."""

KEYRING_SERVICE = "odouche"
"""The service the session is stored under in the OS keyring."""

KEYRING_ENTRY = "session"
"""The name of the keyring entry, which holds the session and the time it was stored."""

MAX_AGE = timedelta(days=30)

_WAIT = 10.0
"""Seconds a keyring call is waited for when nobody is known to be at its dialog."""

_WORKER = "odouche-keyring"

_LOCKED = "The keyring is waiting to be unlocked. Answer its dialog, or supply the session through the environment."

_UNFINISHED = "The keyring has not finished writing, and the stored session may have changed. Log in again."

# The calls left behind that have not ended: the next call waits for them, not beside them.
_left: list[Event] = []


class UnreadableError(NoSessionError):
    """Raised when the stored entry is not a session. It has been deleted."""


@dataclass(frozen=True, slots=True)
class Resolved:
    """A session, where it came from and, when it is the stored one, when it was stored and passes the max age."""

    session: Secret
    source: SessionSource
    stored_at: datetime | None = None
    expires_at: datetime | None = None


def _accepted() -> "type[KeyringBackend]":
    """Name this platform's backend. `keyring.get_keyring()` would also pick a plaintext one."""
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring as Keychain  # noqa: PLC0415

        return Keychain
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring  # noqa: PLC0415

        return WinVaultKeyring
    from keyring.backends.SecretService import Keyring as SecretService  # noqa: PLC0415

    return SecretService


class _Gate:
    """Settles once whether a call may write: its worker commits, unless its caller gave up first."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._committed: bool | None = None

    def commit(self) -> bool:
        with self._lock:
            if self._committed is None:
                self._committed = True
            return self._committed

    def abandon(self) -> bool:
        with self._lock:
            if self._committed is None:
                self._committed = False
            return not self._committed


def _use[T](action: "Callable[[KeyringBackend], T]", *, wait: float | None = None, writes: bool = False) -> T:
    """Run one action on the accepted backend, waiting `wait` seconds, `_WAIT` by default, for a keyring showing a dialog.

    The action runs in a thread that is left behind at the bound, since a blocked keyring call
    cannot be interrupted. One that `writes` reads first, which is what the dialog blocks, and
    writes only if the wait has not been given up. A call left behind is waited for by the next
    one, so a dialog nobody answers holds one thread.

    Only the name of a failure leaves the thread: the backend's exception can hold the entry. No
    caller binds the entry to a name either, since a frame's locals travel with its traceback.
    """
    backend = _accepted()
    if not backend.viable:
        raise KeyringUnavailableError
    deadline = time.monotonic() + (_WAIT if wait is None else wait)
    for left in list(_left):
        if not left.wait(max(0.0, deadline - time.monotonic())):
            raise KeyringUnavailableError(_LOCKED)
        with suppress(ValueError):
            _left.remove(left)
    gate = _Gate()
    done = Event()
    results: list[T] = []
    failures: list[str | BaseException] = []

    def work() -> None:
        try:
            keyring = backend()
            if writes:
                _unlock(keyring)
                if not gate.commit():
                    return
            results.append(action(keyring))
        # Backends let through more than `KeyringError`: D-Bus, pywin32 and decoding errors.
        except Exception as error:  # noqa: BLE001
            failures.append(type(error).__name__)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)
        finally:
            done.set()

    Thread(target=work, name=_WORKER, daemon=True).start()
    try:
        finished = done.wait(max(0.0, deadline - time.monotonic()))
    except BaseException:
        gate.abandon()
        _left.append(done)
        raise
    if not finished:
        _left.append(done)
        if gate.abandon():
            raise KeyringUnavailableError(_LOCKED)
        # The write has started, so the keyring is unlocked.
        if not done.wait(_WAIT):
            raise KeyringUnavailableError(_UNFINISHED)
    for failure in failures:
        if isinstance(failure, BaseException):
            raise failure
        raise KeyringUnavailableError(
            f"The keyring could not be used ({failure}). Unlock it, or supply the session through the environment."
        )
    return results[0]


def _get(backend: "KeyringBackend") -> tuple[bool, tuple[Secret, datetime] | None]:
    """Return whether there is an entry, and what it holds if it can be read."""
    entry = backend.get_password(KEYRING_SERVICE, KEYRING_ENTRY)
    return entry is not None, _read(entry)


def _entry(backend: "KeyringBackend") -> Secret | None:
    """Return the entry as it is stored, wrapped."""
    entry = backend.get_password(KEYRING_SERVICE, KEYRING_ENTRY)
    return None if entry is None else Secret(entry)


def _unlock(backend: "KeyringBackend") -> None:
    """Read the entry and keep nothing of it: a locked keyring asks to be unlocked, or fails."""
    backend.get_password(KEYRING_SERVICE, KEYRING_ENTRY)


def _delete(backend: "KeyringBackend") -> None:
    from keyring.errors import PasswordDeleteError  # noqa: PLC0415

    try:
        backend.delete_password(KEYRING_SERVICE, KEYRING_ENTRY)
    except PasswordDeleteError:
        # Raised when there is no entry, and on macOS when the deletion is refused.
        if backend.get_password(KEYRING_SERVICE, KEYRING_ENTRY) is not None:
            raise


def _read(entry: str | None) -> tuple[Secret, datetime] | None:
    """Read a stored entry, or nothing if it is not one."""
    try:
        stored = json.loads(entry or "")
    except ValueError:
        return None
    if not isinstance(stored, dict):
        return None
    fields = cast("dict[str, object]", stored)
    session, stored_at = fields.get("session"), fields.get("stored_at")
    if not isinstance(session, str) or not session or type(stored_at) is not int:
        return None
    if not is_sendable(Secret(session)):
        return None
    try:
        return Secret(session), datetime.fromtimestamp(stored_at, UTC)
    except (OverflowError, OSError, ValueError):
        return None


class SessionStore:
    """Resolves the session: the one passed, then the environment, then the keyring.

    Only `save` writes, and only to the keyring. `max_age` can be shortened, not lengthened.
    """

    def __init__(
        self,
        session: Secret | None = None,
        *,
        max_age: timedelta = MAX_AGE,
        environ: Mapping[str, str] = os.environ,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not timedelta(0) < max_age <= MAX_AGE:
            raise ValueError(f"max_age is positive and at most {MAX_AGE.days} days")
        self._session = session
        self._max_age = max_age
        self._environ = environ
        self._clock = clock

    def load(self) -> Resolved:
        """Return the session, without ever updating the time it was stored.

        A stored session past the max age is deleted and raises `SessionExpiredError`.
        """
        given = self.given()
        if given is not None:
            return given
        found, stored = _use(_get)
        if not found:
            raise NoSessionError("Not logged in.")
        if stored is None:
            _use(_delete, writes=True)
            raise UnreadableError("The stored session could not be read and was deleted. Log in again.")
        session, stored_at = stored
        # A time in the future is a clock that moved or an entry that was edited.
        if not timedelta(0) <= self._clock() - stored_at <= self._max_age:
            _use(_delete, writes=True)
            raise SessionExpiredError("The stored session passed its max age and was deleted. Log in again.")
        return Resolved(session, SessionSource.KEYRING, stored_at, stored_at + self._max_age)

    def check(self, wait: float | None = None) -> None:
        """Raise `KeyringUnavailableError` if a session could not be saved: no accepted backend, or a locked one.

        A keyring asking to be unlocked is waited for `wait` seconds.
        """
        _use(_unlock, wait=wait)

    def save(self, session: Secret, wait: float | None = None) -> None:
        """Store the session in the keyring with the current time, replacing the stored one.

        It is stored even when another session is passed or set in the environment. If it cannot
        be written, the stored one is put back.
        """
        stored_at = int(self._clock().timestamp())

        def replace(backend: "KeyringBackend") -> None:
            previous = _entry(backend)
            if previous is not None:
                # Credential Locker keeps the entry it overwrites under a second name.
                _delete(backend)
            try:
                backend.set_password(
                    KEYRING_SERVICE,
                    KEYRING_ENTRY,
                    json.dumps({"session": session.expose_secret(), "stored_at": stored_at}),
                )
            except BaseException:
                if previous is not None:
                    with suppress(Exception):
                        backend.set_password(KEYRING_SERVICE, KEYRING_ENTRY, previous.expose_secret())
                raise

        _use(replace, wait=wait, writes=True)

    def discard(self) -> None:
        """Delete the stored session, if that is the one in use. Safe to call twice."""
        if self.given() is None:
            with suppress(KeyringUnavailableError):
                self.delete()

    def delete(self) -> None:
        """Delete the stored session, whichever is in use. Safe to call twice."""
        _use(_delete, writes=True)

    def given(self) -> Resolved | None:
        """Return the session passed or set in the environment, if there is one."""
        if self._session is not None:
            return Resolved(_sendable(self._session, "The session passed"), SessionSource.ARGUMENT)
        value = self._environ.get(SESSION_ENV)
        if not value:
            return None
        return Resolved(_sendable(Secret(value), f"The value of {SESSION_ENV}"), SessionSource.ENVIRONMENT)


def _sendable(session: Secret, what: str) -> Secret:
    if not is_sendable(session):
        raise NoSessionError(f"{what} is not a session_id cookie value.")
    return session
