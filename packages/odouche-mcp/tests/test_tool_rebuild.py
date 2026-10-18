import asyncio
import io
from datetime import UTC, datetime
from typing import Any, ClassVar, Self

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, Tool, ToolAnnotations

import odouche
from odouche_mcp._contract import add_tool
from odouche_mcp._write import CHANGING, changing_tool
from odouche_mcp.server import create_server


SENTINEL = "sentinel-session-value"
SWITCH = ("allow", "change", "write", "read_only", "readonly")

MAIN = odouche.Branch(id=3, name="main", stage=odouche.Stage.PRODUCTION, stage_name="production")
FEATURE = odouche.Branch(id=4, name="feature-x", stage=odouche.Stage.DEVELOPMENT, stage_name="dev")
ODD = odouche.Branch(id=5, name="odd\x1b[2J\nname", stage=odouche.Stage.DEVELOPMENT, stage_name="dev")

STARTED = odouche.Build(
    id=12,
    name="acme-feature-x-12",
    branch_id=FEATURE.id,
    branch_name=FEATURE.name,
    commit=odouche.Commit(
        hash="a" * 40,
        message="Fix it",
        author="Ada",
        timestamp=datetime(2026, 1, 1, 12, tzinfo=UTC),
        url=f"https://github.com/acme/odoo/commit/{'a' * 40}",
    ),
    status=odouche.BuildStatus.PROGRESS,
    status_name="progress",
    result=None,
    result_name=None,
    status_info=None,
    started_at=None,
    url=None,
)


class Upstream:
    """A client over one project, `acme`, that refuses a rebuild as the library does."""

    modes: ClassVar[list[bool]] = []
    rebuilt: ClassVar[list[odouche.Branch]] = []
    failure: ClassVar[odouche.OdoucheError | None] = None

    def __init__(self, *, read_only: bool) -> None:
        self.read_only = read_only
        self.modes.append(read_only)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def branches(self, project: str) -> list[odouche.Branch]:
        if project != "acme":
            msg = f"No project {project} among those you can reach."
            raise odouche.NotFoundError(msg)
        return [MAIN, FEATURE, ODD]

    def rebuild(self, branch: odouche.Branch) -> odouche.Build:
        if self.read_only:
            msg = "This client is read-only: it does not send a rebuild."
            raise odouche.ReadOnlyError(msg)
        if branch.stage is odouche.Stage.PRODUCTION:
            msg = "A branch in the production stage is not rebuilt."
            raise odouche.StageRefusedError(msg)
        self.rebuilt.append(branch)
        if self.failure:
            raise self.failure
        return STARTED


@pytest.fixture(autouse=True)
def upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(odouche.SESSION_ENV, SENTINEL)
    monkeypatch.setattr(odouche, "Client", Upstream)
    monkeypatch.setattr(Upstream, "modes", [])
    monkeypatch.setattr(Upstream, "rebuilt", [])
    monkeypatch.setattr(Upstream, "failure", None)


def _allowed() -> MCPServer:
    return create_server(allow_changes=True)


def _tools(server: MCPServer) -> dict[str, Tool]:
    async def run() -> dict[str, Tool]:
        async with Client(server) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    return asyncio.run(run())


def _call(server: MCPServer, **arguments: Any) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool("rebuild_branch", arguments)

    return asyncio.run(run())


def _error(server: MCPServer, **arguments: Any) -> str:
    result = _call(server, **arguments)
    assert result.is_error
    return result.model_dump()["content"][0]["text"].removeprefix("Error executing tool rebuild_branch: ")


def test_the_tool_is_absent_unless_changes_are_allowed():
    assert "rebuild_branch" not in _tools(create_server())


def test_the_tool_is_listed_as_one_that_changes_state():
    tool = _tools(_allowed())["rebuild_branch"]

    assert tool.annotations == ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
    )
    assert (tool.description or "").startswith("Change state on Odoo.sh: start a new build of a branch")
    assert "Only a development or a staging branch is rebuilt." in (tool.description or "")
    assert "Touches: starts a new build of the branch" in (tool.description or "")
    assert set(tool.input_schema["properties"]) == {"project", "branch"}
    assert set(tool.input_schema["required"]) == {"project", "branch"}


def test_a_rebuild_is_sent_once_and_returns_the_new_build():
    result = _call(_allowed(), project="acme", branch="feature-x")

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["build"]["id"] == STARTED.id
    assert result.structured_content["build"]["status"] == "progress"
    assert Upstream.rebuilt == [FEATURE]
    assert Upstream.modes == [False]


def test_the_tool_built_without_the_switch_has_a_read_only_client():
    server = MCPServer(name="scratch")
    add_tool(
        server,
        changing_tool(allow_changes=False),
        returns="the new build.",
        touches="starts a new build.",
        bounds="five requests.",
        annotations=CHANGING,
    )

    assert _error(server, project="acme", branch="feature-x") == "This client is read-only: it does not send a rebuild."
    assert Upstream.modes == [True]
    assert Upstream.rebuilt == []


def test_a_production_branch_is_refused():
    assert "production" in _error(_allowed(), project="acme", branch="main")
    assert Upstream.rebuilt == []


def test_an_unknown_branch_is_not_found():
    assert _error(_allowed(), project="acme", branch="nope") == "Project acme has no branch nope."
    assert Upstream.rebuilt == []


def test_an_unknown_outcome_says_to_look_before_trying_again(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Upstream, "failure", odouche.OutcomeUnknownError("The rebuild was sent, and not confirmed."))

    error = _error(_allowed(), project="acme", branch="feature-x")

    assert error.startswith("The rebuild was sent, and not confirmed. List the branch's builds")
    assert error.endswith("before trying again.")
    assert Upstream.rebuilt == [FEATURE]


def test_a_rebuild_writes_one_line_to_stderr(capsys: pytest.CaptureFixture[str]):
    _call(_allowed(), project="acme", branch="feature-x")

    captured = capsys.readouterr()
    assert captured.err == "odouche-mcp: rebuild_branch project='acme' branch='feature-x': build 12 started\n"
    assert captured.out == ""


@pytest.mark.parametrize(
    ("branch", "failure", "outcome"),
    [
        ("feature-x", odouche.OutcomeUnknownError(f"Not confirmed for {SENTINEL}."), "OutcomeUnknownError"),
        ("main", None, "StageRefusedError"),
        ("nope", None, "NotFoundError"),
    ],
)
def test_a_rebuild_that_fails_writes_one_line_with_the_kind_of_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    branch: str,
    failure: odouche.OdoucheError | None,
    outcome: str,
):
    monkeypatch.setattr(Upstream, "failure", failure)

    _error(_allowed(), project="acme", branch=branch)

    err = capsys.readouterr().err
    assert err == f"odouche-mcp: rebuild_branch project='acme' branch='{branch}': {outcome}\n"
    assert SENTINEL not in err


def test_the_line_escapes_the_control_characters_of_a_name(capsys: pytest.CaptureFixture[str]):
    _call(_allowed(), project="acme", branch=ODD.name)

    err = capsys.readouterr().err
    assert err == "odouche-mcp: rebuild_branch project='acme' branch='odd\\x1b[2J\\nname': build 12 started\n"


def test_a_stderr_that_is_closed_does_not_fail_a_rebuild_that_was_sent(monkeypatch: pytest.MonkeyPatch):
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr("sys.stderr", closed)

    result = _call(_allowed(), project="acme", branch="feature-x")

    assert not result.is_error
    assert Upstream.rebuilt == [FEATURE]


@pytest.mark.parametrize("allow_changes", [False, True])
def test_no_tool_or_argument_sets_the_switch(*, allow_changes: bool):
    tools = _tools(create_server(allow_changes=allow_changes))

    names = [name for tool in tools.values() for name in (tool.name, *tool.input_schema.get("properties", {}))]

    assert [name for name in names if any(word in name.lower() for word in SWITCH)] == []
