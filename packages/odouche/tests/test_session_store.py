import json
import sys
import threading
import traceback
from datetime import timedelta

import keyring
import pytest
from keyring.errors import KeyringLocked

import odouche
from odouche import (
    KEYRING_ENTRY,
    KEYRING_SERVICE,
    SESSION_ENV,
    KeyringUnavailableError,
    NoSessionError,
    Secret,
    SessionExpiredError,
    SessionSource,
    _session,
)
from odouche._session import MAX_AGE, SessionStore


SESSION = "s3ss10n-v4lu3"
OTHER = "0th3r-v4lu3"
KEY = (KEYRING_SERVICE, KEYRING_ENTRY)


@pytest.fixture(autouse=True)
def no_file_written(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", SESSION_ENV):
        monkeypatch.delenv(name, raising=False)
    yield
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def stored(backend, clock):
    SessionStore(clock=clock).save(Secret(SESSION))
    return backend


def shown(error):
    """Everything an error can print, the locals of the library's frames included."""
    assert error.__context__ is None
    assert error.__cause__ is None
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)]
    local = [repr(frame.f_locals) for frame in frames if frame.f_code.co_filename == _session.__file__]
    assert local
    return [str(error), repr(error), "".join(traceback.format_exception(error)), *local]


@pytest.mark.parametrize("name", ["SESSION_ENV", "KEYRING_SERVICE", "KEYRING_ENTRY"])
def test_constants_are_exported(name):
    assert name in odouche.__all__


def test_survives_a_run(stored, clock):
    stored_at = clock.now
    clock.now += MAX_AGE

    resolved = SessionStore(clock=clock).load()

    assert resolved.session == Secret(SESSION)
    assert resolved.source is SessionSource.KEYRING
    assert resolved.expires_at == stored_at + MAX_AGE


def test_stores_one_entry_with_the_session_and_the_time(stored, clock):
    clock.now += timedelta(days=1)
    SessionStore(clock=clock).save(Secret(OTHER))

    assert list(stored.entries) == [KEY]
    assert SESSION not in repr(stored.entries)
    assert json.loads(stored.entries[KEY]) == {"session": OTHER, "stored_at": int(clock.now.timestamp())}


def test_ages_out(stored, clock):
    clock.now += MAX_AGE + timedelta(seconds=1)
    store = SessionStore(clock=clock)

    with pytest.raises(SessionExpiredError) as raised:
        store.load()

    assert stored.entries == {}
    assert all(SESSION not in text for text in shown(raised.value))
    with pytest.raises(NoSessionError):
        store.load()


def test_a_caller_can_shorten_the_max_age(stored, clock):
    clock.now += timedelta(hours=2)

    assert SessionStore(max_age=timedelta(hours=2), clock=clock).load().expires_at == clock.now
    with pytest.raises(SessionExpiredError):
        SessionStore(max_age=timedelta(hours=1), clock=clock).load()


@pytest.mark.parametrize("max_age", [MAX_AGE + timedelta(seconds=1), timedelta(0), timedelta(days=-1)])
def test_a_caller_cannot_lengthen_the_max_age(max_age):
    with pytest.raises(ValueError, match="max_age"):
        SessionStore(max_age=max_age)


def test_a_session_stored_in_the_future_is_expired(stored, clock):
    clock.now -= timedelta(minutes=1)

    with pytest.raises(SessionExpiredError):
        SessionStore(clock=clock).load()

    assert stored.entries == {}


def test_loading_does_not_extend_the_session(stored, clock):
    entry = stored.entries[KEY]
    clock.now += timedelta(days=1)
    store = SessionStore(clock=clock)

    assert store.load() == store.load()
    assert stored.entries[KEY] == entry


def test_nothing_stored_is_no_session(backend):
    with pytest.raises(NoSessionError):
        SessionStore().load()


def test_the_environment_stays_in_memory(stored, clock, monkeypatch):
    entries, calls = dict(stored.entries), stored.calls
    monkeypatch.setenv(SESSION_ENV, OTHER)
    store = SessionStore(clock=clock)

    resolved = store.load()
    store.discard()

    assert resolved.session == Secret(OTHER)
    assert resolved.source is SessionSource.ENVIRONMENT
    assert resolved.expires_at is None
    assert stored.entries == entries
    assert stored.calls == calls


def test_a_login_is_stored_whatever_the_environment_holds(backend, clock, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, OTHER)

    SessionStore(clock=clock).save(Secret(SESSION))

    assert json.loads(backend.entries[KEY])["session"] == SESSION


def test_the_environment_needs_no_keyring(no_backend, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, SESSION)

    assert SessionStore().load().session == Secret(SESSION)


def test_an_empty_environment_variable_is_unset(stored, clock, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "")

    assert SessionStore(clock=clock).load().source is SessionSource.KEYRING


def test_a_passed_session_wins_and_is_never_stored(stored, clock, monkeypatch):
    entries, calls = dict(stored.entries), stored.calls
    monkeypatch.setenv(SESSION_ENV, SESSION)
    store = SessionStore(Secret(OTHER), clock=clock)

    resolved = store.load()
    store.discard()

    assert resolved.session == Secret(OTHER)
    assert resolved.source is SessionSource.ARGUMENT
    assert stored.entries == entries
    assert stored.calls == calls


def test_discard_deletes_the_entry_and_is_safe_twice(stored, clock):
    store = SessionStore(clock=clock)

    store.discard()
    store.discard()

    assert stored.entries == {}


def test_check_passes_with_a_keyring_and_changes_no_entry(stored):
    entries = dict(stored.entries)

    SessionStore().check()

    assert stored.entries == entries


def test_check_raises_with_a_keyring_that_stays_locked(stored):
    stored.failure = KeyringLocked

    with pytest.raises(KeyringUnavailableError, match="KeyringLocked") as raised:
        SessionStore().check()

    assert all(SESSION not in text for text in shown(raised.value))


@pytest.fixture
def locked(stored, monkeypatch):
    monkeypatch.setattr(_session, "_WAIT", 0.01)
    stored.lock()
    yield stored
    stored.unlock()


@pytest.mark.parametrize(
    "use",
    [SessionStore.check, SessionStore.load, lambda store: store.save(Secret(OTHER))],
    ids=["check", "load", "save"],
)
def test_a_keyring_showing_a_dialog_is_not_waited_for_without_end(locked, clock, use):
    entries = dict(locked.entries)

    with pytest.raises(KeyringUnavailableError, match=r"waiting to be unlocked.*dialog.*environment") as raised:
        use(SessionStore(clock=clock))

    assert all(value not in text for text in shown(raised.value) for value in (SESSION, OTHER))
    locked.unlock()
    assert locked.entries == entries


def test_a_discard_given_up_on_deletes_nothing_later(locked, clock):
    entries = dict(locked.entries)

    SessionStore(clock=clock).discard()
    locked.unlock()

    assert locked.entries == entries


def test_a_dialog_answered_within_the_wait_lets_the_call_through(locked, clock):
    threading.Timer(0.01, locked.dialog.set).start()

    SessionStore(clock=clock).save(Secret(OTHER), 60)

    assert json.loads(locked.entries[KEY])["session"] == OTHER


def test_a_write_that_has_started_is_waited_for(stored, clock, monkeypatch):
    written = threading.Event()
    write = stored.set_password

    def slow(self, service, username, password):
        written.wait()
        write(self, service, username, password)

    monkeypatch.setattr(stored, "set_password", slow)
    threading.Timer(0.05, written.set).start()

    SessionStore(clock=clock).save(Secret(OTHER), 0.01)

    assert json.loads(stored.entries[KEY])["session"] == OTHER


def test_a_write_that_does_not_finish_is_not_reported_as_nothing_stored(stored, clock, monkeypatch):
    written = threading.Event()
    monkeypatch.setattr(_session, "_WAIT", 0.01)
    monkeypatch.setattr(stored, "set_password", lambda *_: written.wait())

    with pytest.raises(KeyringUnavailableError, match="may have changed") as raised:
        SessionStore(clock=clock).save(Secret(OTHER))

    assert all(value not in text for text in shown(raised.value) for value in (SESSION, OTHER))
    written.set()
    stored.unlock()


def test_an_interrupted_wait_writes_nothing_later(locked, clock, monkeypatch):
    entries = dict(locked.entries)

    class Interrupted(threading.Event):
        interrupts = 1

        def wait(self, timeout=None):
            if self.interrupts:
                self.interrupts = 0
                raise KeyboardInterrupt
            return super().wait(timeout)

    monkeypatch.setattr(_session, "Event", Interrupted)

    with pytest.raises(KeyboardInterrupt):
        SessionStore(clock=clock).save(Secret(OTHER))

    monkeypatch.undo()
    locked.unlock()
    assert locked.entries == entries


def test_a_dialog_nobody_answers_holds_one_thread(locked, clock):
    store = SessionStore(clock=clock)

    for _ in range(3):
        with pytest.raises(KeyringUnavailableError, match="waiting to be unlocked"):
            store.load()

    assert [thread.name for thread in threading.enumerate()].count(_session._WORKER) == 1
    locked.unlock()
    assert store.load().session == Secret(SESSION)


def test_a_session_that_cannot_be_written_leaves_the_stored_one(stored, clock):
    entries = dict(stored.entries)
    stored.failing_writes = 1

    with pytest.raises(KeyringUnavailableError, match="PasswordSetError") as raised:
        SessionStore(clock=clock).save(Secret(OTHER))

    assert stored.entries == entries
    assert SessionStore(clock=clock).load().session == Secret(SESSION)
    assert all(value not in text for text in shown(raised.value) for value in (SESSION, OTHER))


def test_an_interrupted_write_leaves_the_stored_session(stored, clock):
    entries = dict(stored.entries)
    stored.failing_writes = 1
    stored.write_failure = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        SessionStore(clock=clock).save(Secret(OTHER))

    assert stored.entries == entries


def test_a_keyring_that_takes_no_write_at_all_shows_nothing_of_either_session(stored, clock):
    stored.failing_writes = 2

    with pytest.raises(KeyringUnavailableError, match="PasswordSetError") as raised:
        SessionStore(clock=clock).save(Secret(OTHER))

    assert stored.entries == {}
    assert all(value not in text for text in shown(raised.value) for value in (SESSION, OTHER))


def test_check_raises_without_a_keyring(no_backend):
    with pytest.raises(KeyringUnavailableError, match="Secret Service provider"):
        SessionStore().check()


def test_discard_without_a_keyring_does_nothing(no_backend):
    SessionStore().discard()

    assert no_backend.calls == 0


@pytest.mark.parametrize("use", [SessionStore.load, lambda store: store.save(Secret(SESSION))], ids=["load", "save"])
def test_no_plaintext(no_backend, memory, monkeypatch, use):
    plaintext = memory()
    monkeypatch.setattr(keyring.core, "_keyring_backend", plaintext())
    assert isinstance(keyring.get_keyring(), plaintext)

    with pytest.raises(KeyringUnavailableError) as raised:
        use(SessionStore())

    assert "Secret Service provider" in str(raised.value)
    assert plaintext.calls == 0
    assert no_backend.calls == 0


@pytest.mark.parametrize(
    ("platform", "name"),
    [
        ("linux", "keyring.backends.SecretService.Keyring"),
        ("darwin", "keyring.backends.macOS.Keyring"),
        ("win32", "keyring.backends.Windows.WinVaultKeyring"),
    ],
)
def test_accepts_one_backend_per_platform(monkeypatch, platform, name):
    monkeypatch.setattr(sys, "platform", platform)

    backend = _session._accepted()

    assert f"{backend.__module__}.{backend.__qualname__}" == name


class DBusError(Exception):
    """Stands in for what a backend lets through that is not a `KeyringError`."""


@pytest.mark.parametrize("failure", [KeyringLocked, DBusError, UnicodeError])
@pytest.mark.parametrize("use", [SessionStore.load, lambda store: store.save(Secret(SESSION))], ids=["load", "save"])
def test_a_keyring_that_fails_shows_nothing_of_the_entry(stored, clock, failure, use):
    stored.failure = failure

    with pytest.raises(KeyringUnavailableError) as raised:
        use(SessionStore(clock=clock))

    assert failure.__name__ in str(raised.value)
    assert all(SESSION not in text for text in shown(raised.value))


@pytest.mark.parametrize("failure", [KeyringLocked, DBusError])
def test_discard_with_a_keyring_that_fails_does_not_raise(stored, failure):
    stored.failure = failure

    SessionStore().discard()


def test_an_entry_that_cannot_be_deleted_is_not_reported_deleted(stored, clock):
    stored.refuses_deletion = True
    clock.now += MAX_AGE + timedelta(seconds=1)
    store = SessionStore(clock=clock)

    with pytest.raises(KeyringUnavailableError) as raised:
        store.load()

    assert "PasswordDeleteError" in str(raised.value)
    assert all(SESSION not in text for text in shown(raised.value))
    store.discard()


@pytest.mark.parametrize(
    "entry",
    [
        SESSION,
        f'{{"session": "{SESSION}"',
        json.dumps([SESSION]),
        json.dumps({"session": SESSION}),
        json.dumps({"session": SESSION, "stored_at": "2026-01-01"}),
        json.dumps({"session": SESSION, "stored_at": True}),
        json.dumps({"session": SESSION, "stored_at": 10**20}),
        json.dumps({"session": "", "stored_at": 0}),
        json.dumps({"session": 42, "stored_at": 0}),
    ],
)
def test_an_unreadable_entry_is_deleted_and_shows_nothing(backend, caplog, entry):
    backend.entries[KEY] = entry

    with caplog.at_level("DEBUG"), pytest.raises(NoSessionError) as raised:
        SessionStore().load()

    assert backend.entries == {}
    assert all(SESSION not in text for text in [*shown(raised.value), caplog.text])


def test_does_not_show_the_session(stored, clock):
    store = SessionStore(Secret(SESSION), clock=clock)

    assert SESSION not in repr(store)
    assert SESSION not in repr(vars(store))
    assert SESSION not in repr(SessionStore(clock=clock).load())


NOT_SENDABLE = ["s\u00e9ss10n", f"{SESSION}; tz=UTC", f"{SESSION}\r\nX-Injected: 1"]


@pytest.mark.parametrize("value", NOT_SENDABLE)
def test_a_passed_value_that_is_not_a_cookie_value_is_no_session(backend, value):
    with pytest.raises(NoSessionError, match="The session passed") as raised:
        SessionStore(Secret(value)).load()

    assert value not in "".join(traceback.format_exception(raised.value))
    assert backend.calls == 0


@pytest.mark.parametrize("value", NOT_SENDABLE)
def test_an_environment_value_that_is_not_a_cookie_value_is_no_session(backend, value):
    with pytest.raises(NoSessionError, match=SESSION_ENV) as raised:
        SessionStore(environ={SESSION_ENV: value}).load()

    assert value not in "".join(traceback.format_exception(raised.value))
    assert backend.calls == 0


@pytest.mark.parametrize("value", NOT_SENDABLE)
def test_a_stored_value_that_is_not_a_cookie_value_is_deleted(backend, clock, value):
    backend.entries[KEY] = json.dumps({"session": value, "stored_at": int(clock().timestamp())})

    with pytest.raises(NoSessionError) as raised:
        SessionStore(clock=clock).load()

    assert value not in "".join(traceback.format_exception(raised.value))
    assert KEY not in backend.entries
