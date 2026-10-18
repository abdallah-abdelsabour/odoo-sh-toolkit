import dataclasses
import json
import traceback
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from odouche import (
    SESSION_ENV,
    Client,
    Identity,
    KeyringUnavailableError,
    LogoutResult,
    NoSessionError,
    Secret,
    SessionExpiredError,
    SessionInfo,
    SessionSource,
    UpstreamChangedError,
    UpstreamUnavailableError,
    logout,
)
from odouche._session import KEYRING_ENTRY, KEYRING_SERVICE, MAX_AGE, SessionStore


KEY = (KEYRING_SERVICE, KEYRING_ENTRY)
PROFILE = "/app/user/profile"
LOGOUT = "/web/session/logout"
NOTHING = LogoutResult(source=None, deleted=False, invalidated=False, failure=None)


@pytest.fixture
def stored(upstream, backend, session):
    SessionStore().save(Secret(session))
    return backend


def test_names_the_user_and_where_the_session_came_from(client, upstream, session):
    identity = client.identity()

    assert identity == Identity(
        user_id=7301,
        name="Octo Dev",
        username="octo-dev",
        email="dev@example.com",
        session=SessionInfo(SessionSource.ARGUMENT, None, None),
    )
    assert session not in repr(identity)
    (request,) = upstream.requests
    assert (request.method, request.url.path) == ("POST", PROFILE)
    assert request.headers["Cookie"] == f"session_id={session}"


def test_an_identity_is_immutable_and_holds_no_payload(client):
    identity = client.identity()

    assert [field.name for field in dataclasses.fields(identity)] == ["user_id", "name", "username", "email", "session"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.name = "other"


def test_the_stored_session_says_when_it_was_stored_and_expires_without_a_request(stored, upstream, session):
    before = datetime.now(UTC).replace(microsecond=0)

    info = Client().session

    assert info.source is SessionSource.KEYRING
    assert info.stored_at is not None
    assert before <= info.stored_at <= datetime.now(UTC)
    assert info.expires_at == info.stored_at + MAX_AGE
    assert session not in repr(info)
    assert upstream.requests == []


def test_a_session_from_the_environment_has_no_stored_time(upstream, backend, monkeypatch, session):
    monkeypatch.setenv(SESSION_ENV, session)

    assert Client().session == SessionInfo(SessionSource.ENVIRONMENT, None, None)
    assert Client().identity().session.source is SessionSource.ENVIRONMENT
    assert backend.calls == 0


def test_a_rejected_stored_session_is_deleted(stored, upstream):
    upstream.bodies[PROFILE] = upstream.load("unauthenticated.json")

    with pytest.raises(SessionExpiredError):
        Client().identity()

    assert KEY not in stored.entries


@pytest.mark.parametrize("absent", [False, None])
def test_a_user_with_no_name_or_email_has_none(client, upstream, absent):
    upstream.bodies[PROFILE]["result"].update(name=absent, email=absent)

    identity = client.identity()

    assert (identity.name, identity.email, identity.username) == (None, None, "octo-dev")


@pytest.mark.parametrize(
    ("field", "changed"),
    [("id", False), ("username", False), ("username", None), ("name", 0), ("email", ["dev@example.com"])],
)
def test_names_the_field_that_changed_shape(client, upstream, session, field, changed):
    upstream.bodies[PROFILE]["result"][field] = changed

    with pytest.raises(UpstreamChangedError) as raised:
        client.identity()

    assert raised.value.operation == "identity"
    assert raised.value.field == f"result.{field}"
    text = "".join(traceback.format_exception(raised.value))
    for value in ("octo-dev", "dev@example.com", session):
        assert value not in text


def test_logout_ends_the_session_on_both_sides(stored, upstream, session):
    assert logout() == LogoutResult(source=SessionSource.KEYRING, deleted=True, invalidated=True, failure=None)

    assert KEY not in stored.entries
    (request,) = upstream.requests
    assert (request.method, request.url.path, request.url.query) == ("GET", LOGOUT, b"redirect=%2F")
    assert request.headers["Cookie"] == f"session_id={session}"
    with pytest.raises(NoSessionError):
        Client()


@pytest.mark.parametrize("failure", [httpx2.ConnectError, 503])
def test_logout_deletes_the_session_when_odoo_sh_cannot_be_asked(stored, upstream, session, failure):
    upstream.failures[LOGOUT] = failure

    result = logout()

    assert (result.source, result.deleted, result.invalidated) == (SessionSource.KEYRING, True, False)
    assert isinstance(result.failure, UpstreamUnavailableError)
    assert result.failure.operation == "logout"
    assert session not in "".join(traceback.format_exception(result.failure))
    assert session not in repr(result)
    assert KEY not in stored.entries
    assert len(upstream.requests) == 3


def test_logout_reports_an_answer_it_does_not_know_and_still_deletes(stored, upstream):
    upstream.failures[LOGOUT] = 200

    result = logout()

    assert isinstance(result.failure, UpstreamChangedError)
    assert (result.deleted, result.invalidated) == (True, False)
    assert KEY not in stored.entries


def test_logout_of_a_session_odoo_sh_already_rejects_is_a_logout(stored, upstream, monkeypatch):
    monkeypatch.setattr(
        upstream,
        "_answer",
        lambda request: httpx2.Response(303, headers={"Location": "/web/login?redirect=%2F"}),
    )

    assert logout() == LogoutResult(source=SessionSource.KEYRING, deleted=True, invalidated=True, failure=None)
    assert KEY not in stored.entries


def test_logout_leaves_a_session_from_the_environment_to_the_caller(stored, upstream, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "0th3r-v4lu3")

    assert logout() == LogoutResult(source=SessionSource.ENVIRONMENT, deleted=False, invalidated=False, failure=None)
    assert upstream.requests == []
    assert KEY in stored.entries


def test_logout_leaves_a_passed_session_to_the_caller(stored, upstream):
    result = logout(Secret("0th3r-v4lu3"))

    assert result == LogoutResult(source=SessionSource.ARGUMENT, deleted=False, invalidated=False, failure=None)
    assert upstream.requests == []
    assert KEY in stored.entries


@pytest.mark.parametrize("source", [SessionSource.ARGUMENT, SessionSource.ENVIRONMENT])
def test_logout_ends_a_given_session_only_when_asked(stored, upstream, monkeypatch, source):
    given = None
    if source is SessionSource.ARGUMENT:
        given = Secret("0th3r-v4lu3")
    else:
        monkeypatch.setenv(SESSION_ENV, "0th3r-v4lu3")

    result = logout(given, invalidate_given=True)

    assert result == LogoutResult(source=source, deleted=False, invalidated=True, failure=None)
    (request,) = upstream.requests
    assert request.headers["Cookie"] == "session_id=0th3r-v4lu3"
    assert KEY in stored.entries


def test_logout_deletes_the_session_when_the_request_is_interrupted(stored, upstream):
    upstream.failures[LOGOUT] = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        logout()

    assert KEY not in stored.entries


def test_an_interrupted_logout_stays_an_interrupt_when_the_session_cannot_be_deleted(stored, upstream):
    upstream.failures[LOGOUT] = KeyboardInterrupt
    stored.refuses_deletion = True

    with pytest.raises(KeyboardInterrupt):
        logout()


@pytest.mark.parametrize(("failure", "ended"), [(None, "was ended"), (503, "was not ended")])
def test_logout_says_when_the_session_could_not_be_deleted(stored, upstream, session, failure, ended):
    stored.refuses_deletion = True
    if failure:
        upstream.failures[LOGOUT] = failure

    with pytest.raises(KeyringUnavailableError, match=f"The session {ended} on Odoo.sh") as raised:
        logout()

    assert KEY in stored.entries
    assert session not in "".join(traceback.format_exception(raised.value))


def test_logout_sent_elsewhere_is_reported_and_still_deletes(stored, upstream, monkeypatch):
    monkeypatch.setattr(
        upstream, "_answer", lambda request: httpx2.Response(303, headers={"Location": "/web/session/confirm"})
    )

    result = logout()

    assert isinstance(result.failure, UpstreamChangedError)
    assert result.failure.field == "Location"
    assert (result.deleted, result.invalidated) == (True, False)
    assert KEY not in stored.entries


def test_logout_twice_is_not_an_error(stored, upstream):
    logout()

    assert logout() == NOTHING
    assert len(upstream.requests) == 1


def test_logout_with_no_session_asks_nothing(upstream, backend):
    assert logout() == NOTHING
    assert upstream.requests == []


def test_logout_past_the_max_age_deletes_without_asking(upstream, backend, session):
    stored_at = int((datetime.now(UTC) - MAX_AGE - timedelta(days=1)).timestamp())
    backend.entries[KEY] = json.dumps({"session": session, "stored_at": stored_at})

    assert logout() == LogoutResult(source=SessionSource.KEYRING, deleted=True, invalidated=False, failure=None)
    assert KEY not in backend.entries
    assert upstream.requests == []


def test_logout_of_a_stored_entry_that_cannot_be_read_says_it_was_deleted(upstream, backend):
    backend.entries[KEY] = "not an entry"

    assert logout() == LogoutResult(source=SessionSource.KEYRING, deleted=True, invalidated=False, failure=None)
    assert KEY not in backend.entries
    assert upstream.requests == []


@pytest.mark.parametrize("passed", [True, False])
def test_logout_of_a_given_value_that_is_not_a_cookie_value_says_so(stored, upstream, monkeypatch, passed):
    if not passed:
        monkeypatch.setenv(SESSION_ENV, "n0t a c00k13")

    with pytest.raises(NoSessionError, match="not a session_id cookie value"):
        logout(Secret("n0t a c00k13") if passed else None)

    assert upstream.requests == []
    assert KEY in stored.entries


def test_logout_without_a_keyring_says_so(upstream, no_backend):
    with pytest.raises(KeyringUnavailableError):
        logout()
