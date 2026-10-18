import asyncio

import pytest
from mcp.server import MCPServer

import odouche_mcp
from odouche_mcp.server import create_server, main


def test_server_identifies_itself():
    server = create_server()

    assert server.name == "odouche"
    assert server.version == odouche_mcp.__version__


READS = [
    "get_session",
    "list_projects",
    "list_branches",
    "list_builds",
    "get_build",
    "wait_for_build",
    "read_log",
    "get_firewall",
]


def _names(server: MCPServer) -> list[str]:
    return [tool.name for tool in asyncio.run(server.list_tools())]


def test_server_exposes_its_tools():
    assert _names(create_server()) == READS


def test_server_exposes_the_tool_that_changes_state_only_when_allowed():
    assert _names(create_server(allow_changes=True)) == [*READS, "rebuild_branch", "update_firewall"]


@pytest.mark.parametrize(
    ("argv", "tools"), [([], READS), (["--allow-changes"], [*READS, "rebuild_branch", "update_firewall"])]
)
def test_main_serves_over_stdio(monkeypatch: pytest.MonkeyPatch, argv: list[str], tools: list[str]):
    served: list[tuple[list[str], str]] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport: served.append((_names(self), transport)))

    main(argv)

    assert served == [(tools, "stdio")]


@pytest.mark.parametrize("argv", [["--allow"], ["--allow-change"], ["--allow-changes=1"]])
def test_main_refuses_any_other_spelling_of_the_switch(monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    served: list[str] = []
    monkeypatch.setattr(MCPServer, "run", lambda _self, transport: served.append(transport))

    with pytest.raises(SystemExit) as exit_:
        main(argv)

    assert exit_.value.code == 2
    assert served == []


def test_main_reads_the_arguments_of_the_process(monkeypatch: pytest.MonkeyPatch):
    served: list[list[str]] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport: served.append(_names(self)))
    monkeypatch.setattr("sys.argv", ["odouche-mcp", "--allow-changes"])

    main()

    assert served == [[*READS, "rebuild_branch", "update_firewall"]]
