import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import pytest
from mcp import Client
from mcp.types import CallToolResult

import odouche
from odouche_mcp.server import create_server


NONE = odouche.NoSessionError("Not logged in.")
EXPIRED = odouche.SessionExpiredError("The stored session passed its max age and was deleted. Log in again.")
NO_KEYRING = odouche.KeyringUnavailableError()
REJECTED = odouche.SessionExpiredError("Odoo.sh rejected the session. Log in again.")


def _identity(source: odouche.SessionSource, expires_at: datetime | None) -> odouche.Identity:
    stored_at = expires_at and expires_at - timedelta(days=7)
    return odouche.Identity(
        user_id=7,
        name="Ada",
        username="ada",
        email="ada@example.com",
        session=odouche.SessionInfo(source, stored_at, expires_at),
    )


class Store:
    """A client over a store in one state: no session, an expired one, no keyring, or a valid one."""

    missing: odouche.OdoucheError | None = None
    rejected: odouche.OdoucheError | None = None
    identified = _identity(odouche.SessionSource.ENVIRONMENT, None)

    def __init__(self, *, read_only: bool) -> None:
        assert read_only
        if self.missing:
            raise self.missing

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def identity(self) -> odouche.Identity:
        if self.rejected:
            raise self.rejected
        return self.identified


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr(odouche, "Client", Store)
    monkeypatch.delenv(odouche.SESSION_ENV, raising=False)
    return monkeypatch


def _call(*states: dict[str, Any]) -> list[CallToolResult]:
    """Call `get_session` on one server, once per state of the store."""

    async def run() -> list[CallToolResult]:
        results: list[CallToolResult] = []
        async with Client(create_server()) as client:
            for state in states:
                with pytest.MonkeyPatch.context() as patch:
                    for name, value in state.items():
                        patch.setattr(Store, name, value)
                    results.append(await client.call_tool("get_session"))
        return results

    return asyncio.run(run())


def _says_how_to_log_in(text: str) -> bool:
    return "osh auth login" in text and odouche.SESSION_ENV in text and "Only the user can log in" in text


@pytest.mark.usefixtures("store")
def test_the_server_starts_and_lists_its_tools_logged_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Store, "missing", NONE)

    async def names() -> list[str]:
        async with Client(create_server()) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert "get_session" in asyncio.run(names())


@pytest.mark.usefixtures("store")
@pytest.mark.parametrize("missing", [NONE, EXPIRED, NO_KEYRING], ids=["none", "expired", "no keyring"])
def test_with_no_session_the_status_says_what_the_user_does(missing: odouche.OdoucheError):
    (result,) = _call({"missing": missing})

    assert not result.is_error
    status = result.structured_content
    assert status is not None
    assert status["available"] is False
    assert status["identity"] is None
    assert status["seconds_left"] is None
    assert status["problem"].startswith(f"{missing} ")
    assert _says_how_to_log_in(status["problem"])


@pytest.mark.usefixtures("store")
def test_a_session_odoo_sh_rejects_is_an_error_that_says_what_the_user_does():
    (result,) = _call({"rejected": REJECTED})

    assert result.is_error
    text = result.model_dump()["content"][0]["text"]
    assert f"{REJECTED} " in text
    assert _says_how_to_log_in(text)


MALFORMED = odouche.NoSessionError(f"The value of {odouche.SESSION_ENV} is not a session_id cookie value.")


@pytest.mark.parametrize(("state", "failed"), [({"missing": MALFORMED}, False), ({"rejected": REJECTED}, True)])
def test_a_session_from_the_environment_is_not_fixed_by_a_login(
    store: pytest.MonkeyPatch, *, state: dict[str, Any], failed: bool
):
    store.setenv(odouche.SESSION_ENV, "not-a-session")

    (result,) = _call(state)

    assert result.is_error == failed
    text = result.model_dump()["content"][0]["text"]
    assert f"The session comes from {odouche.SESSION_ENV}" in text
    assert "restart this server" in text


@pytest.mark.usefixtures("store")
def test_a_login_is_picked_up_without_a_restart():
    before, after = _call({"missing": NONE}, {})

    assert before.structured_content is not None
    assert before.structured_content["available"] is False
    assert after.structured_content is not None
    assert after.structured_content["available"] is True


@pytest.mark.usefixtures("store")
def test_a_session_from_the_environment_has_no_time_left_to_report():
    (result,) = _call({})

    assert result.structured_content == {
        "available": True,
        "identity": {
            "user_id": 7,
            "name": "Ada",
            "username": "ada",
            "email": "ada@example.com",
            "session": {"source": "environment", "stored_at": None, "expires_at": None},
        },
        "seconds_left": None,
        "problem": None,
    }


@pytest.mark.usefixtures("store")
@pytest.mark.parametrize(("left", "low", "high"), [(timedelta(hours=1), 3590, 3600), (-timedelta(hours=1), 0, 0)])
def test_a_stored_session_reports_the_time_left(left: timedelta, low: int, high: int):
    identified = _identity(odouche.SessionSource.KEYRING, datetime.now(UTC) + left)

    (result,) = _call({"identified": identified})

    status = result.structured_content
    assert status is not None
    assert status["identity"]["session"]["source"] == "keyring"
    assert low <= status["seconds_left"] <= high


@pytest.mark.usefixtures("store")
def test_nothing_is_written_to_stdout(capfd: pytest.CaptureFixture[str]):
    _call({"missing": NONE}, {"missing": EXPIRED}, {"missing": NO_KEYRING}, {"rejected": REJECTED}, {})

    assert capfd.readouterr().out == ""
