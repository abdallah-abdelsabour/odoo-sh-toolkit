import asyncio
from typing import Any, ClassVar, Self

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, Tool, ToolAnnotations

import odouche
from odouche_mcp._contract import add_tool
from odouche_mcp._write import FIREWALL, firewall_tool
from odouche_mcp.server import create_server


SENTINEL = "sentinel-session-value"
KEPT = ("1.1.1.1", "8.8.8.8")
MINE = "9.9.9.9"


class Upstream:
    """A client over one project, `acme`, whose firewall changes as the library changes it."""

    modes: ClassVar[list[bool]] = []
    sent: ClassVar[list[tuple[str, str, bool]]] = []
    addresses: ClassVar[tuple[str, ...]] = KEPT
    failure: ClassVar[odouche.OdoucheError | None] = None

    def __init__(self, *, read_only: bool) -> None:
        self.read_only = read_only
        self.modes.append(read_only)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def firewall(self, project: str) -> odouche.Firewall:
        if project != "acme":
            msg = f"The session's user can reach no project named {project!r}."
            raise odouche.NotFoundError(msg)
        return odouche.Firewall(project, type(self).addresses)

    # The library's own, which asks nothing.
    plan_allow_ip = odouche.Client.plan_allow_ip

    def allow_ip(self, project: str, address: str, *, replace: bool = True) -> odouche.Firewall:
        if self.read_only:
            msg = "This client is read-only: it does not send a allow."
            raise odouche.ReadOnlyError(msg)
        self.sent.append((project, address, replace))
        if self.failure:
            raise self.failure
        changed = self.plan_allow_ip(self.firewall(project), address, replace=replace)
        type(self).addresses = changed.addresses
        return changed


@pytest.fixture(autouse=True)
def upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(odouche.SESSION_ENV, SENTINEL)
    monkeypatch.setattr(odouche, "Client", Upstream)
    monkeypatch.setattr(Upstream, "modes", [])
    monkeypatch.setattr(Upstream, "sent", [])
    monkeypatch.setattr(Upstream, "addresses", KEPT)
    monkeypatch.setattr(Upstream, "failure", None)


def _allowed() -> MCPServer:
    return create_server(allow_changes=True)


def _tools(server: MCPServer) -> dict[str, Tool]:
    async def run() -> dict[str, Tool]:
        async with Client(server) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    return asyncio.run(run())


def _call(server: MCPServer, tool: str, **arguments: Any) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def _error(server: MCPServer, tool: str, **arguments: Any) -> str:
    result = _call(server, tool, **arguments)
    assert result.is_error
    return result.model_dump()["content"][0]["text"].removeprefix(f"Error executing tool {tool}: ")


def test_reading_the_firewall_is_always_there_and_read_only():
    tool = _tools(create_server())["get_firewall"]
    result = _call(create_server(), "get_firewall", project="acme")

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert result.structured_content == {"firewall": {"project": "acme", "addresses": list(KEPT)}}
    assert Upstream.modes == [True]


def test_an_unknown_project_is_not_found():
    assert _error(create_server(), "get_firewall", project="nope") == (
        "The session's user can reach no project named 'nope'."
    )


def test_updating_is_absent_unless_changes_are_allowed():
    assert "update_firewall" not in _tools(create_server())


def test_updating_is_listed_as_a_change_that_is_safe_to_repeat():
    tool = _tools(_allowed())["update_firewall"]

    assert tool.annotations == ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
    )
    description = tool.description or ""
    assert description.startswith("Change state on Odoo.sh: let a public IPv4 address")
    assert "this server does not look one up" in description
    assert "Touches: the project's firewall" in description
    assert set(tool.input_schema["properties"]) == {"project", "address", "replace"}
    assert set(tool.input_schema["required"]) == {"project", "address"}


def test_an_address_is_added_to_the_others_by_default():
    result = _call(_allowed(), "update_firewall", project="acme", address=MINE)

    assert not result.is_error
    assert result.structured_content == {
        "firewall": {"project": "acme", "addresses": [*KEPT, MINE]},
        "changed": True,
    }
    assert Upstream.sent == [("acme", MINE, False)]
    assert Upstream.modes == [False]


def test_replace_leaves_the_address_alone():
    result = _call(_allowed(), "update_firewall", project="acme", address=MINE, replace=True)

    assert result.structured_content == {"firewall": {"project": "acme", "addresses": [MINE]}, "changed": True}
    assert Upstream.sent == [("acme", MINE, True)]


def test_an_address_already_allowed_sends_nothing():
    result = _call(_allowed(), "update_firewall", project="acme", address=KEPT[1])

    assert result.structured_content == {"firewall": {"project": "acme", "addresses": list(KEPT)}, "changed": False}
    assert Upstream.sent == []


@pytest.mark.parametrize(("address", "said"), [("10.0.0.1", "is not public"), ("nine.nine", "is not an IPv4 address")])
def test_an_address_odoo_sh_would_refuse_is_refused_before_sending(address: str, said: str):
    assert said in _error(_allowed(), "update_firewall", project="acme", address=address)
    assert Upstream.sent == []


def test_a_full_firewall_is_refused():
    Upstream.addresses = tuple(f"1.1.1.{number}" for number in range(1, 11))

    assert "holds 10 addresses" in _error(_allowed(), "update_firewall", project="acme", address=MINE)
    assert Upstream.sent == []


def test_an_unknown_outcome_says_to_read_the_firewall(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        Upstream, "failure", odouche.OutcomeUnknownError("It was sent, and not confirmed.", operation="firewall change")
    )

    error = _error(_allowed(), "update_firewall", project="acme", address=MINE)

    assert error.startswith("It was sent, and not confirmed. Read the firewall with get_firewall")
    assert "branch" not in error


def test_the_tool_built_without_the_switch_has_a_read_only_client():
    server = MCPServer(name="scratch")
    add_tool(
        server,
        firewall_tool(allow_changes=False),
        returns="the firewall.",
        touches="the firewall.",
        bounds="three requests.",
        annotations=FIREWALL,
    )

    assert "read-only" in _error(server, "update_firewall", project="acme", address=MINE)
    assert Upstream.modes == [True]
    assert Upstream.sent == []


@pytest.mark.parametrize(("address", "outcome"), [(MINE, "changed"), (KEPT[0], "unchanged"), ("10.0.0.1", "ToolError")])
def test_each_call_writes_one_line_to_stderr(capsys: pytest.CaptureFixture[str], address: str, outcome: str):
    _call(_allowed(), "update_firewall", project="acme", address=address)

    captured = capsys.readouterr()
    assert captured.err == f"odouche-mcp: update_firewall project='acme' address={address!r} replace=False: {outcome}\n"
    assert SENTINEL not in captured.err
    assert captured.out == ""
