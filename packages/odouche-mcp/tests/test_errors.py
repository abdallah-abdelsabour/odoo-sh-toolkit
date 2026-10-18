import asyncio

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import odouche
from odouche_mcp._errors import NEXT_STEPS, tool_error


CASES: list[tuple[odouche.OdoucheError, str | None]] = [
    (odouche.NoSessionError("No session."), odouche.SESSION_ENV),
    (odouche.SessionExpiredError("The session has expired."), "osh auth login"),
    (odouche.NotFoundError("No such project."), None),
    (odouche.PermissionDeniedError("Not allowed."), None),
    (odouche.UpstreamChangedError("projects", "name"), None),
    (odouche.UpstreamUnavailableError("Odoo.sh cannot be reached."), "Try again later."),
    (odouche.StreamTimeoutError("Still running."), None),
    (odouche.LoginTimeoutError("Nobody logged in."), None),
    (odouche.ReadOnlyError("Read-only."), None),
    (odouche.StageRefusedError("A production branch."), None),
    (odouche.FirewallRefusedError("The firewall is full."), None),
    (odouche.OutcomeUnknownError("Not confirmed."), "before trying again"),
    (odouche.KeyringUnavailableError(), odouche.SESSION_ENV),
    (odouche.LoginError("No session captured."), None),
    (odouche.OdoucheError("Something else."), None),
]


@pytest.mark.parametrize(("error", "step"), CASES, ids=lambda value: type(value).__name__)
def test_a_library_error_is_its_message_and_the_next_step(
    monkeypatch: pytest.MonkeyPatch, error: odouche.OdoucheError, step: str | None
):
    monkeypatch.delenv(odouche.SESSION_ENV, raising=False)
    mapped = tool_error(error)

    assert isinstance(mapped, ToolError)
    if step is None:
        assert str(mapped) == str(error)
    else:
        assert str(mapped).startswith(f"{error} ")
        assert step in str(mapped)


def test_every_library_error_has_its_own_row():
    exported = {
        value
        for name in odouche.__all__
        if isinstance(value := getattr(odouche, name), type) and issubclass(value, odouche.OdoucheError)
    }

    assert {kind for kind, _ in NEXT_STEPS} == exported
    assert {type(error) for error, _ in CASES} == exported
    assert NEXT_STEPS[-1][0] is odouche.OdoucheError


def test_an_unexpected_error_reaches_the_client_without_its_text():
    server = MCPServer(name="scratch")

    def break_things() -> None:
        msg = "what went wrong inside"
        raise RuntimeError(msg)

    server.add_tool(break_things)

    async def call() -> str:
        async with Client(server) as client:
            result = await client.call_tool("break_things")
            assert result.is_error
            return result.model_dump_json()

    dumped = asyncio.run(call())

    assert "Error executing tool break_things" in dumped
    assert "what went wrong inside" not in dumped
    assert "Traceback" not in dumped
