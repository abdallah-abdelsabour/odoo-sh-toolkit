"""The contract, checked over whatever a server has registered.

A tool added to `create_server()` is covered by the first two tests once `RESULTS` holds what it
asks the client.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import InvalidSignature
from mcp.types import Tool, ToolAnnotations

import odouche
from odouche_mcp._contract import BOUNDS, NAME, READ_ONLY, READS_ONLY, RETURNS, TOUCHES, add_tool
from odouche_mcp._errors import NEXT_STEPS
from odouche_mcp.server import create_server


SENTINEL = "sentinel-session-value"

PROJECT = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")

IDENTITY = odouche.Identity(
    user_id=7,
    name="Ada",
    username="ada",
    email="ada@example.com",
    session=odouche.SessionInfo(odouche.SessionSource.ENVIRONMENT, None, None),
)

# Named as the project, which is the one string a tool is called with.
BRANCH = odouche.Branch(id=3, name="acme", stage=odouche.Stage.DEVELOPMENT, stage_name="dev")

BUILD = odouche.Build(
    id=5,
    name="acme-acme-5",
    branch_id=BRANCH.id,
    branch_name=BRANCH.name,
    commit=odouche.Commit(
        hash="0" * 40,
        message="Fix it",
        author="Ada",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        url=f"https://github.com/acme/odoo/commit/{'0' * 40}",
    ),
    status=odouche.BuildStatus.DONE,
    status_name="done",
    result=odouche.BuildResult.SUCCESS,
    result_name="success",
    status_info=None,
    started_at=None,
    url=None,
)

# What the stand-in client answers, by method.
RESULTS: dict[str, object] = {
    "projects": [PROJECT],
    "identity": IDENTITY,
    "branches": [BRANCH],
    "builds": [BUILD],
    "latest_build": BUILD,
    "rebuild": BUILD,
    "firewall": odouche.Firewall("acme", ("8.8.8.8",)),
    "plan_allow_ip": odouche.Firewall("acme", ("8.8.8.8", "9.9.9.9")),
    "allow_ip": odouche.Firewall("acme", ("8.8.8.8", "9.9.9.9")),
    "logs": [odouche.Log(odouche.LogKind.INSTALL, "install", datetime(2026, 1, 1, tzinfo=UTC), "1 KB")],
    "read_log": [odouche.LogLine("Loading modules", offset=16, truncated=False)],
}

# One of each error the library raises.
FAILURES: list[odouche.OdoucheError] = [
    odouche.UpstreamChangedError("projects", "name") if kind is odouche.UpstreamChangedError else kind("It failed.")
    for kind, _ in NEXT_STEPS
]

SAMPLES: dict[str, object] = {"string": "acme", "integer": 1, "number": 1.0, "boolean": False}


class StandIn:
    """A client that holds the sentinel as its session, and answers from `RESULTS` or raises `failure`."""

    failure: odouche.OdoucheError | None = None

    def __init__(self, *_: object, **__: object) -> None:
        self.secret = odouche.Secret(SENTINEL)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def __getattr__(self, name: str) -> Callable[..., object]:
        if name not in RESULTS:
            raise AttributeError(name)

        def answer(*_: object, **__: object) -> object:
            if self.failure:
                raise self.failure
            return RESULTS[name]

        return answer


async def _tools(server: MCPServer[Any]) -> list[Tool]:
    async with Client(server) as client:
        return (await client.list_tools()).tools


def violations(server: MCPServer[Any]) -> list[str]:
    """Return what each registered tool lacks of the contract, each line starting with the tool's name."""
    found: list[str] = []
    for tool in asyncio.run(_tools(server)):
        lines = dict(line.split(": ", 1) for line in (tool.description or "").splitlines() if ": " in line)
        hints = tool.annotations or ToolAnnotations()
        if not NAME.fullmatch(tool.name):
            found.append(f"{tool.name}: not named verb then noun")
        found.extend(
            f"{tool.name}: no `{label}:` line"
            for label in (RETURNS, TOUCHES, BOUNDS)
            if not lines.get(label, "").strip()
        )
        if None in {hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint}:
            found.append(f"{tool.name}: a hint is not set")
        if bool(hints.read_only_hint) != (lines.get(TOUCHES) == READS_ONLY):
            found.append(f"{tool.name}: the read-only hint and the `{TOUCHES}:` line disagree")
        if (tool.output_schema or {}).get("type") != "object":
            found.append(f"{tool.name}: no structured result")
        found.extend(
            f"{tool.name}: takes `{name}`, and a session is never an argument"
            for name in tool.input_schema.get("properties", {})
            if any(word in name.lower() for word in ("session", "cookie"))
        )
    return found


def _arguments(tool: Tool) -> dict[str, object]:
    properties: dict[str, dict[str, Any]] = tool.input_schema.get("properties", {})
    return {name: SAMPLES[properties[name]["type"]] for name in tool.input_schema.get("required", [])}


async def _leaks(server: MCPServer[Any]) -> list[str]:
    found: list[str] = []
    async with Client(server) as client:
        for tool in (await client.list_tools()).tools:
            for failure in (None, *FAILURES):
                StandIn.failure = failure
                case = f"{tool.name}, {type(failure).__name__ if failure else 'answered'}"
                result = await client.call_tool(tool.name, _arguments(tool))
                dumped = result.model_dump_json()
                if SENTINEL in dumped:
                    found.append(f"{case}: the session is in the result")
                if "Traceback" in dumped:
                    found.append(f"{case}: a traceback is in the result")
                if result.is_error != bool(failure):
                    found.append(f"{case}: {'no error' if failure else 'an error'}")
                if failure and str(failure) not in result.model_dump()["content"][0]["text"]:
                    found.append(f"{case}: the error is not the library's message")
    return found


def leaks(server: MCPServer[Any]) -> list[str]:
    """Call every registered tool on a session that is the sentinel, answered then failed with each library error."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(odouche.SESSION_ENV, SENTINEL)
        patch.setattr(odouche, "Client", StandIn)
        patch.setattr(StandIn, "failure", None)
        return asyncio.run(_leaks(server))


@dataclass(frozen=True)
class Projects:
    projects: list[odouche.Project]


@dataclass(frozen=True)
class Leak:
    value: str


def list_projects() -> Projects:
    """List the projects.

    Not part of the description.
    """
    with odouche.Client(read_only=True) as client:
        return Projects(client.projects())


def scratch() -> MCPServer[Any]:
    return MCPServer(name="scratch")


def add(server: MCPServer[Any], fn: Callable[..., object], **changes: Any) -> None:
    add_tool(server, fn, **{"returns": "the projects.", "bounds": "one request.", **changes})


@pytest.mark.parametrize("allow_changes", [False, True])
def test_the_server_meets_the_contract(*, allow_changes: bool):
    assert violations(create_server(allow_changes=allow_changes)) == []


@pytest.mark.parametrize("allow_changes", [False, True])
def test_the_server_leaks_no_session(*, allow_changes: bool):
    assert leaks(create_server(allow_changes=allow_changes)) == []


def test_every_tool_is_read_only_unless_changes_are_allowed():
    tools = asyncio.run(_tools(create_server()))

    assert [tool.name for tool in tools if not (tool.annotations and tool.annotations.read_only_hint)] == []


def test_a_tool_registered_through_the_helper_meets_the_contract():
    server = scratch()
    add(server, list_projects)

    assert violations(server) == []
    assert leaks(server) == []


def test_the_description_is_the_summary_and_the_labelled_lines():
    server = scratch()
    add(server, list_projects)

    (tool,) = asyncio.run(_tools(server))

    assert tool.name == "list_projects"
    assert tool.description == (
        "List the projects.\n\nReturns: the projects.\nTouches: reads only\nBounds: one request."
    )
    assert tool.annotations == READ_ONLY


def test_the_result_has_the_fields_of_the_library_models():
    server = scratch()
    add(server, list_projects)

    async def call() -> object:
        async with Client(server) as client:
            return (await client.call_tool("list_projects")).structured_content

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(odouche, "Client", StandIn)
        result = asyncio.run(call())

    assert result == {
        "projects": [{"id": 1, "name": "acme", "repository": "acme/odoo", "url": "https://www.odoo.sh/project/acme"}]
    }


def test_a_tool_registered_without_the_helper_fails_and_is_named():
    server = scratch()

    def get_nothing() -> None:
        """Get nothing."""

    server.add_tool(get_nothing)

    found = violations(server)

    assert all(line.startswith("get_nothing: ") for line in found)
    assert {line.removeprefix("get_nothing: ") for line in found} == {
        "no `Returns:` line",
        "no `Touches:` line",
        "no `Bounds:` line",
        "a hint is not set",
    }


def test_a_tool_with_no_schema_for_its_result_fails():
    server = scratch()

    def get_anything():
        """Get anything."""

    server.add_tool(get_anything, description="Get.\n\nReturns: it.\nTouches: reads only\nBounds: none.")

    assert "get_anything: no structured result" in violations(server)


def test_a_tool_that_takes_a_session_is_caught():
    server = scratch()

    def list_builds(session_id: str) -> Projects:
        """List the builds."""
        del session_id
        return Projects([])

    add(server, list_builds)

    assert violations(server) == ["list_builds: takes `session_id`, and a session is never an argument"]


@pytest.mark.parametrize(
    ("name", "description", "annotations", "violation"),
    [
        ("projects", "Returns: it.\nTouches: reads only\nBounds: none.", READ_ONLY, "not named verb then noun"),
        ("listProjects", "Returns: it.\nTouches: reads only\nBounds: none.", READ_ONLY, "not named verb then noun"),
        ("list_projects", "Returns: it.\nTouches: a build\nBounds: none.", READ_ONLY, "disagree"),
        (
            "list_projects",
            "Returns: it.\nTouches: reads only\nBounds: none.",
            READ_ONLY.model_copy(update={"read_only_hint": False}),
            "disagree",
        ),
        ("list_projects", "Returns: it.\nTouches: reads only\nBounds: ", READ_ONLY, "no `Bounds:` line"),
    ],
)
def test_each_rule_is_checked(name: str, description: str, annotations: ToolAnnotations, violation: str):
    server = scratch()
    server.add_tool(list_projects, name=name, description=description, annotations=annotations)

    (found,) = violations(server)

    assert found.startswith(f"{name}: ")
    assert violation in found


def test_a_tool_that_returns_the_session_is_caught():
    server = scratch()

    def get_session() -> Leak:
        """Get what no tool may return."""
        with odouche.Client() as client:
            client.projects()
            secret: odouche.Secret = client.secret  # pyright: ignore[reportAttributeAccessIssue]
            return Leak(secret.expose_secret())

    add(server, get_session)

    assert "get_session, answered: the session is in the result" in leaks(server)


def test_a_tool_that_hides_a_library_error_is_caught():
    server = scratch()

    def get_calm() -> Projects:
        """Get an answer whatever happened."""
        try:
            return list_projects()
        except odouche.OdoucheError:
            return Projects([])

    add(server, get_calm)

    assert "get_calm, NotFoundError: no error" in leaks(server)


def test_a_tool_with_no_stand_in_answer_is_caught():
    server = scratch()

    def follow_log() -> Projects:
        """Follow what the stand-in does not answer."""
        with odouche.Client() as client:
            client.follow_log("acme", BUILD, odouche.LogKind.ODOO, timeout=1)
        return Projects([])

    add(server, follow_log)

    assert "follow_log, answered: an error" in leaks(server)


def get_undocumented() -> Projects:
    return Projects([])


def get_project() -> odouche.Project:
    """Get a project, as the bare model the server cannot describe."""
    return PROJECT


@pytest.mark.parametrize(
    ("fn", "changes", "error"),
    [
        (lambda: None, {}, ValueError),
        (get_undocumented, {}, ValueError),
        (list_projects, {"returns": " "}, ValueError),
        (list_projects, {"bounds": "one\nrequest"}, ValueError),
        (list_projects, {"touches": "the branch's builds"}, ValueError),
        (list_projects, {"annotations": READ_ONLY.model_copy(update={"read_only_hint": False})}, ValueError),
        (list_projects, {"annotations": ToolAnnotations(read_only_hint=True)}, ValueError),
        (get_project, {}, InvalidSignature),
    ],
)
def test_the_helper_refuses_what_breaks_the_contract(
    fn: Callable[..., object], changes: dict[str, Any], error: type[Exception]
):
    server = scratch()

    with pytest.raises(error):
        add(server, fn, **changes)

    assert asyncio.run(_tools(server)) == []


def test_the_helper_refuses_a_name_that_is_registered():
    server = scratch()
    add(server, list_projects)

    with pytest.raises(ValueError, match="list_projects"):
        add(server, list_projects)

    assert len(asyncio.run(_tools(server))) == 1


def test_the_helper_refuses_a_coroutine():
    async def wait_long() -> Projects:
        """Wait."""
        return Projects([])

    with pytest.raises(TypeError):
        add(scratch(), wait_long)


def test_a_tool_that_changes_something_says_what():
    server = scratch()
    changing = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
    )
    add(server, list_projects, touches="starts a build of the branch.", annotations=changing)

    assert violations(server) == []
    assert asyncio.run(_tools(server))[0].annotations == changing
