import copy
import pickle
import traceback

import pytest

import odouche
from odouche import (
    KeyringUnavailableError,
    LoginError,
    LoginTimeoutError,
    NoSessionError,
    NotFoundError,
    OdoucheError,
    OutcomeUnknownError,
    PermissionDeniedError,
    ReadOnlyError,
    SessionExpiredError,
    StageRefusedError,
    StreamTimeoutError,
    UpstreamChangedError,
    UpstreamUnavailableError,
)


COOKIE = "s3ss10n-v4lu3"
HEADER = "X-Csrf-Token"
OPERATION = "projects"
FIELD = "result[0].name"

BUILDERS = {
    NoSessionError: lambda: NoSessionError("Not logged in.", operation=OPERATION),
    SessionExpiredError: lambda: SessionExpiredError("The session expired.", operation=OPERATION, status=200),
    NotFoundError: lambda: NotFoundError("No such project.", operation=OPERATION, status=404),
    PermissionDeniedError: lambda: PermissionDeniedError("Not allowed.", operation=OPERATION, status=403),
    UpstreamChangedError: lambda: UpstreamChangedError(OPERATION, FIELD, status=200),
    UpstreamUnavailableError: lambda: UpstreamUnavailableError("Odoo.sh is down.", operation=OPERATION, status=502),
    ReadOnlyError: lambda: ReadOnlyError("This client is read-only.", operation="rebuild"),
    StageRefusedError: lambda: StageRefusedError("Not rebuilt.", operation="rebuild"),
    OutcomeUnknownError: lambda: OutcomeUnknownError("Look before trying again.", operation="rebuild", status=503),
    StreamTimeoutError: lambda: StreamTimeoutError("Still running.", operation="watch"),
    KeyringUnavailableError: KeyringUnavailableError,
    LoginError: lambda: LoginError("The browser was closed.", operation="login"),
    LoginTimeoutError: lambda: LoginTimeoutError("The login was not completed.", operation="login"),
}
CLASSES = [OdoucheError, *BUILDERS]


class RequestError(Exception):
    """Stands in for an HTTP client's exception, which keeps the request it failed on."""

    def __init__(self):
        super().__init__(f"request failed, Cookie: session_id={COOKIE}")
        self.request = {"headers": {"Cookie": f"session_id={COOKIE}", HEADER: COOKIE}}


@pytest.fixture(params=BUILDERS.values(), ids=[cls.__name__ for cls in BUILDERS])
def error(request):
    return request.param()


@pytest.mark.parametrize("cls", CLASSES)
def test_is_exported(cls):
    assert cls.__name__ in odouche.__all__
    assert getattr(odouche, cls.__name__) is cls


@pytest.mark.parametrize("cls", CLASSES)
def test_says_when_it_is_raised(cls):
    assert cls.__doc__


def test_is_caught_as_the_base(error):
    with pytest.raises(OdoucheError):
        raise error


@pytest.mark.parametrize("cls", BUILDERS)
def test_is_caught_as_nothing_else(cls):
    assert cls.__bases__ == (OdoucheError,)


def test_no_session_and_expired_session_are_told_apart():
    assert not issubclass(NoSessionError, SessionExpiredError)
    assert not issubclass(SessionExpiredError, NoSessionError)


def test_carries_the_operation_the_status_and_the_message():
    error = NotFoundError("No such project.", operation=OPERATION, status=404)

    assert str(error) == "No such project."
    assert error.operation == OPERATION
    assert error.status == 404


def test_operation_and_status_are_optional():
    error = OdoucheError("Something failed.")

    assert error.operation is None
    assert error.status is None


def test_upstream_changed_names_the_request_and_the_field_and_asks_for_a_report():
    error = UpstreamChangedError(OPERATION, FIELD)

    assert error.operation == OPERATION
    assert error.field == FIELD
    assert OPERATION in str(error)
    assert FIELD in str(error)
    assert "Odoo.sh has probably changed" in str(error)
    assert "https://github.com/avanserv/odouche/issues" in str(error)


def test_keyring_unavailable_names_the_two_ways_out():
    message = str(KeyringUnavailableError())

    assert "Secret Service provider" in message
    assert "environment" in message


def raise_from_a_failed_request(error):
    try:
        raise RequestError
    except RequestError:
        raise error from None


def test_shows_nothing_from_the_failed_request(error):
    with pytest.raises(OdoucheError) as raised:
        raise_from_a_failed_request(error)

    rendered = [str(raised.value), repr(raised.value), "".join(traceback.format_exception(raised.value))]
    for text in rendered:
        assert COOKIE not in text
        assert HEADER not in text
        assert "Cookie" not in text


@pytest.mark.parametrize("duplicate", [copy.copy, lambda error: pickle.loads(pickle.dumps(error))])  # noqa: S301
def test_survives_a_copy_and_a_pickle(error, duplicate):
    duplicated = duplicate(error)

    assert type(duplicated) is type(error)
    assert str(duplicated) == str(error)
    assert vars(duplicated) == vars(error)
