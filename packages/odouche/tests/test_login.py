import inspect
import json
import traceback
from pathlib import Path

import httpx2
import pytest
from keyring.errors import KeyringLocked

import odouche
from odouche import (
    KEYRING_ENTRY,
    KEYRING_SERVICE,
    KeyringUnavailableError,
    LoginError,
    LoginStep,
    LoginTimeoutError,
    PermissionDeniedError,
    Secret,
    UpstreamUnavailableError,
    __version__,
    _login,
    login,
)
from odouche._session import SessionStore
from odouche._upstream.transport import HOST, Transport


SESSION = "s3ss10n-v4lu3"
ANONYMOUS = "4n0nym0us-v4lu3"
PREVIOUS = "pr3v10us-v4lu3"
KEY = (KEYRING_SERVICE, KEYRING_ENTRY)
FIXTURES = Path(__file__).parent / "fixtures"
BROWSER = "/usr/bin/chromium"


class Upstream:
    """Answers the session it knows with the projects, any other as Odoo.sh does a made-up one."""

    def __init__(self):
        self.requests = []
        self.status = 200

    def __call__(self, request):
        self.requests.append(request)
        signed_in = request.headers["Cookie"] == f"session_id={SESSION}"
        name = "projects.json" if signed_in else "unauthenticated.json"
        return httpx2.Response(self.status, content=(FIXTURES / name).read_bytes())

    def connect(self, session):
        return Transport(session, transport=httpx2.MockTransport(self), sleep=lambda _: None)


class Browser:
    """Stands in for the capture: each poll gives the next of `cookies`, the last one repeating."""

    def __init__(self):
        self.cookies = [None]
        self.launched = []
        self.closed = 0
        self.cleanup = None
        self.elapsed = 0.0

    def __call__(self, path):
        self.launched.append(path)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed += 1
        if self.cleanup is not None:
            raise self.cleanup

    def cookie(self):
        value = self.cookies.pop(0) if len(self.cookies) > 1 else self.cookies[0]
        if isinstance(value, BaseException):
            raise value
        return None if value is None else Secret(value)


class Frontend:
    """The callbacks a frontend passes, and what they were called with."""

    def __init__(self):
        self.steps = []
        self.pasted = SESSION
        self.asked = 0

    def ask(self):
        self.asked += 1
        return Secret(self.pasted)


@pytest.fixture
def upstream(monkeypatch):
    upstream = Upstream()
    monkeypatch.setattr(_login, "_transport", upstream.connect)
    return upstream


@pytest.fixture
def browser(monkeypatch):
    browser = Browser()
    monkeypatch.setattr(_login, "_find", lambda: BROWSER)
    monkeypatch.setattr(_login, "_capture", browser)
    monkeypatch.setattr(_login, "_monotonic", lambda: browser.elapsed)
    monkeypatch.setattr(_login, "_sleep", lambda seconds: setattr(browser, "elapsed", browser.elapsed + seconds))
    return browser


@pytest.fixture
def no_browser(monkeypatch, browser):
    monkeypatch.setattr(_login, "_find", lambda: None)
    return browser


@pytest.fixture
def frontend():
    return Frontend()


@pytest.fixture
def previous(backend, clock):
    SessionStore(clock=clock).save(Secret(PREVIOUS))
    return backend.entries[KEY]


def stored(backend):
    return [json.loads(entry)["session"] for entry in backend.entries.values()]


def shown(error):
    """Everything an error can print, the locals of the library's frames included."""
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)]
    local = [repr(frame.f_locals) for frame in frames if frame.f_code.co_filename == _login.__file__]
    assert local
    return [str(error), repr(error), "".join(traceback.format_exception(error)), *local]


@pytest.mark.parametrize("name", ["login", "LoginStep"])
def test_is_exported_and_documented(name):
    assert name in odouche.__all__
    assert getattr(odouche, name).__doc__


def test_takes_callbacks_and_a_timeout_and_nothing_to_point_it_elsewhere():
    assert set(inspect.signature(login).parameters) == {"ask", "notify", "timeout"}


def test_a_browser_login_stores_the_session(backend, upstream, browser, frontend):
    browser.cookies = [None, SESSION]

    login(ask=frontend.ask, notify=frontend.steps.append)

    assert stored(backend) == [SESSION]
    assert browser.launched == [BROWSER]
    assert browser.closed == 1
    assert frontend.steps == [LoginStep.KEYRING, LoginStep.BROWSER]
    assert frontend.asked == 0


def test_the_session_is_not_taken_before_the_sign_in(backend, upstream, browser, previous):
    browser.cookies = [None, ANONYMOUS, ANONYMOUS, ANONYMOUS, SESSION]

    login()

    assert [request.headers["Cookie"] for request in upstream.requests] == [
        f"session_id={ANONYMOUS}",
        f"session_id={SESSION}",
    ]
    assert stored(backend) == [SESSION]


def test_a_captured_value_that_is_not_a_cookie_value_is_never_sent(backend, upstream, browser):
    browser.cookies = [f"{ANONYMOUS}; tz=UTC", SESSION]

    login()

    assert [request.headers["Cookie"] for request in upstream.requests] == [f"session_id={SESSION}"]


def test_verifies_as_itself_with_one_request(backend, upstream, browser):
    browser.cookies = [SESSION]

    login()

    (request,) = upstream.requests
    assert str(request.url) == f"https://{HOST}/app/projects"
    assert request.headers["User-Agent"] == f"odouche/{__version__}"


def test_a_refusal_is_raised_and_not_retried_as_something_else(backend, upstream, browser, previous):
    browser.cookies = [SESSION]
    upstream.status = 403

    with pytest.raises(PermissionDeniedError) as raised:
        login()

    assert len(upstream.requests) == 1
    assert backend.entries[KEY] == previous
    assert browser.closed == 1
    assert all(SESSION not in text for text in shown(raised.value))


def test_an_abandoned_login_ends(backend, upstream, browser, previous):
    browser.cookies = [None, ANONYMOUS]

    with pytest.raises(LoginTimeoutError, match="60 seconds") as raised:
        login(timeout=60)

    assert browser.elapsed == 60
    assert len(upstream.requests) == 1
    assert backend.entries[KEY] == previous
    assert browser.closed == 1
    assert all(ANONYMOUS not in text for text in shown(raised.value))


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), LoginError("The browser was closed.")])
def test_an_interrupted_login_closes_the_browser_and_stores_nothing(backend, upstream, browser, previous, failure):
    browser.cookies = [ANONYMOUS, failure]

    with pytest.raises(type(failure)):
        login()

    assert browser.closed == 1
    assert backend.entries[KEY] == previous


def test_a_profile_left_behind_stores_nothing(backend, upstream, browser):
    browser.cookies = [SESSION]
    browser.cleanup = LoginError("The browser profile could not be deleted.")

    with pytest.raises(LoginError, match="profile"):
        login()

    assert backend.entries == {}


def test_logging_in_again_leaves_one_entry(backend, upstream, browser, previous):
    browser.cookies = [SESSION]

    login()

    assert stored(backend) == [SESSION]


def test_a_stored_session_is_not_discarded_by_a_rejected_one(backend, upstream, no_browser, frontend, previous):
    frontend.pasted = ANONYMOUS

    with pytest.raises(LoginError, match="pasted") as raised:
        login(ask=frontend.ask)

    assert backend.entries[KEY] == previous
    assert all(ANONYMOUS not in text for text in shown(raised.value))


def test_without_a_browser_the_session_is_pasted(backend, upstream, no_browser, frontend):
    login(ask=frontend.ask, notify=frontend.steps.append)

    assert stored(backend) == [SESSION]
    assert frontend.steps == [LoginStep.KEYRING, LoginStep.PASTE]
    assert no_browser.launched == []


def test_without_a_browser_or_a_prompt_there_is_no_login(backend, upstream, no_browser):
    with pytest.raises(LoginError, match="prompt"):
        login()

    assert upstream.requests == []


@pytest.mark.parametrize("value", ["", "a b", f"{SESSION}; tz=UTC", f"{SESSION}\r\nX-Injected: 1", "sëssion"])
def test_a_value_that_is_not_a_cookie_value_is_never_sent(backend, upstream, no_browser, frontend, value):
    frontend.pasted = value

    with pytest.raises(LoginError, match="not a session_id") as raised:
        login(ask=frontend.ask)

    assert upstream.requests == []
    assert backend.entries == {}
    assert not value or all(value not in text for text in shown(raised.value))


def test_with_nowhere_to_store_a_session_nothing_is_asked_of_the_user(no_backend, upstream, browser, frontend):
    browser.cookies = [SESSION]

    with pytest.raises(KeyringUnavailableError):
        login(ask=frontend.ask, notify=frontend.steps.append)

    assert browser.launched == []
    assert frontend.steps == [LoginStep.KEYRING]
    assert upstream.requests == []


def test_with_a_locked_keyring_nothing_is_asked_of_the_user(backend, upstream, browser, frontend):
    backend.failure = KeyringLocked
    browser.cookies = [SESSION]

    with pytest.raises(KeyringUnavailableError, match="KeyringLocked"):
        login(ask=frontend.ask, notify=frontend.steps.append)

    assert browser.launched == []
    assert frontend.steps == [LoginStep.KEYRING]
    assert upstream.requests == []


def test_a_keyring_that_is_never_unlocked_ends_the_login(backend, upstream, browser, frontend):
    backend.lock()
    browser.cookies = [SESSION]

    with pytest.raises(KeyringUnavailableError, match="waiting to be unlocked"):
        login(ask=frontend.ask, notify=frontend.steps.append, timeout=0.01)

    backend.unlock()
    assert browser.launched == []
    assert frontend.steps == [LoginStep.KEYRING]
    assert upstream.requests == []
    assert backend.entries == {}


def test_a_login_waits_for_the_keyring_to_be_unlocked(backend, upstream, browser, frontend):
    backend.lock()
    browser.cookies = [SESSION]

    def notify(step):
        frontend.steps.append(step)
        if step is LoginStep.KEYRING:
            backend.dialog.set()

    login(notify=notify, timeout=60)

    assert frontend.steps == [LoginStep.KEYRING, LoginStep.BROWSER]
    assert stored(backend) == [SESSION]


def test_odoo_sh_being_down_is_not_a_rejected_session(backend, upstream, no_browser, frontend):
    upstream.status = 503

    with pytest.raises(UpstreamUnavailableError):
        login(ask=frontend.ask)

    assert len(upstream.requests) == 3
    assert backend.entries == {}


def test_the_library_stays_silent(backend, upstream, browser, capsys, caplog):
    browser.cookies = [ANONYMOUS, SESSION]

    with caplog.at_level("DEBUG"):
        login()

    assert capsys.readouterr() == ("", "")
    assert caplog.records
    assert SESSION not in caplog.text
    assert ANONYMOUS not in caplog.text
