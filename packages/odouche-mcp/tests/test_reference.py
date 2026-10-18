"""`docs/mcp-reference.md`, held to the server, and the script that writes it."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import pytest
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

import mcp_reference
from odouche_mcp._contract import READ_ONLY, add_tool
from odouche_mcp.server import create_server


CHANGING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)


class Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


@dataclass(frozen=True)
class Thing:
    name: str


def list_things(shelf: str, limit: int = 3, colour: Colour | None = None, *, dusty: bool = False) -> Thing:
    """List the things of a shelf | rack.

    Not shown.
    """
    return Thing(f"{shelf}{limit}{colour}{dusty}")


def paint_thing(thing: str) -> Thing:
    """Paint a thing."""
    return Thing(thing)


def sample() -> MCPServer:
    """Make a server with one tool that reads and one that changes."""
    server = MCPServer(name="sample")
    add_tool(server, list_things, returns="the things.", bounds="`limit` things.")
    add_tool(
        server, paint_thing, returns="the thing.", touches="paints | coats it.", bounds="one.", annotations=CHANGING
    )
    return server


def test_the_committed_page_is_what_the_tools_give():
    committed = mcp_reference.PAGE.read_text(encoding="utf-8")

    assert committed == mcp_reference.render(), (
        f"docs/{mcp_reference.PAGE.name} is stale. Run `{mcp_reference.REGENERATE}`."
    )


def test_every_tool_of_the_server_has_its_row_and_its_section():
    page = mcp_reference.render()

    for tool in asyncio.run(create_server(allow_changes=True).list_tools()):
        assert tool.annotations is not None
        assert tool.description is not None
        touches = tool.description.split("\nTouches: ", 1)[1].split("\n", 1)[0]
        changes = "No" if tool.annotations.read_only_hint else "Yes"
        assert f"| [`{tool.name}`](#{tool.name}) | {changes} | {touches} |" in page
        assert f"\n## `{tool.name}`\n" in page
        assert f"- **Touches**: {touches}\n" in page


def test_a_new_tool_makes_the_committed_page_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    page = tmp_path / "mcp-reference.md"
    page.write_text(mcp_reference.render(), encoding="utf-8")
    monkeypatch.setattr(mcp_reference, "PAGE", page)
    assert mcp_reference.main(["--check"]) == 0

    monkeypatch.setattr(mcp_reference, "create_server", lambda **_: sample())

    assert mcp_reference.main(["--check"]) == 1
    assert mcp_reference.REGENERATE in capsys.readouterr().err
    assert "paint_thing" not in page.read_text(encoding="utf-8")
    assert mcp_reference.main([]) == 0
    assert "## `paint_thing`" in page.read_text(encoding="utf-8")
    assert mcp_reference.main(["--check"]) == 0


def test_a_missing_page_is_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(mcp_reference, "PAGE", tmp_path / "mcp-reference.md")

    assert mcp_reference.main(["--check"]) == 1


def test_the_table_marks_each_tool_as_reading_or_changing():
    page = mcp_reference.render(sample())

    assert (
        """\
| Tool | Changes Odoo.sh | Touches |
| --- | --- | --- |
| [`list_things`](#list_things) | No | reads only |
| [`paint_thing`](#paint_thing) | Yes | paints \\| coats it. |
"""
        in page
    )


def test_a_tool_is_rendered_with_its_arguments_lines_and_hints():
    page = mcp_reference.render(sample())

    assert (
        """\
## `list_things`

List the things of a shelf | rack.

| Argument | Type | Default |
| --- | --- | --- |
| `shelf` | string | Required. |
| `limit` | integer | `3` |
| `colour` | one of `red`, `blue` | |
| `dusty` | boolean | `false` |

- **Returns**: the things.
- **Touches**: reads only
- **Bounds**: `limit` things.

| Hint | Value |
| --- | --- |
| `readOnlyHint` | true |
| `destructiveHint` | false |
| `idempotentHint` | true |
| `openWorldHint` | true |
"""
        in page
    )


def unlabelled() -> MCPServer:
    server = MCPServer(name="sample")
    server.add_tool(paint_thing, description="Paint a thing.\n\nReturns: it.\nBounds: one.", annotations=READ_ONLY)
    return server


def unhinted() -> MCPServer:
    server = MCPServer(name="sample")
    description = "Paint a thing.\n\nReturns: it.\nTouches: it.\nBounds: one."
    server.add_tool(paint_thing, description=description, annotations=ToolAnnotations(read_only_hint=False))
    return server


def angled() -> MCPServer:
    def see_thing() -> Thing:
        """See a thing <b>now</b>."""
        return Thing("")

    server = MCPServer(name="sample")
    add_tool(server, see_thing, returns="it.", bounds="one.")
    return server


def controlled() -> MCPServer:
    server = MCPServer(name="sample")
    description = "Paint a thing.\n\nReturns: it.\nTouches: it\x1b.\nBounds: one."
    server.add_tool(paint_thing, description=description, annotations=CHANGING)
    return server


def listed() -> MCPServer:
    def sort_things(things: list[str]) -> Thing:
        """Sort things."""
        return Thing(things[0])

    server = MCPServer(name="sample")
    add_tool(server, sort_things, returns="it.", bounds="one.")
    return server


def _one(tool: Callable[..., Thing]) -> MCPServer:
    server = MCPServer(name="sample")
    add_tool(server, tool, returns="it.", bounds="one.")
    return server


def short() -> MCPServer:
    server = MCPServer(name="sample")
    server.add_tool(paint_thing, description="Paint a thing.", annotations=READ_ONLY)
    return server


def ranged() -> MCPServer:
    def count_things(limit: Annotated[int, Field(ge=1)] = 3) -> Thing:
        """Count things."""
        return Thing(str(limit))

    return _one(count_things)


def fixed() -> MCPServer:
    def find_thing(thing: Literal["a"]) -> Thing:
        """Find a thing."""
        return Thing(thing)

    return _one(find_thing)


def piped() -> MCPServer:
    def split_thing(separator: str = "a|b") -> Thing:
        """Split a thing."""
        return Thing(separator)

    return _one(split_thing)


def nullable() -> MCPServer:
    def drop_thing(thing: str | None) -> Thing:
        """Drop a thing."""
        return Thing(str(thing))

    return _one(drop_thing)


def mixed() -> MCPServer:
    def pick_thing(thing: int | str) -> Thing:
        """Pick a thing."""
        return Thing(str(thing))

    server = MCPServer(name="sample")
    add_tool(server, pick_thing, returns="it.", bounds="one.")
    return server


@pytest.mark.parametrize(
    ("server", "said"),
    [
        (unlabelled, "`paint_thing` does not end with its `Touches:` line"),
        (short, "`paint_thing` does not end with its `Returns:` line"),
        (ranged, "`limit` of `count_things` has a schema the page cannot show"),
        (fixed, "`thing` of `find_thing` has a schema the page cannot show"),
        (piped, "`separator` of `split_thing` has a choice or a default a table cell cannot hold"),
        (nullable, "`thing` of `drop_thing` has a schema the page cannot show"),
        (unhinted, "`paint_thing` does not set all four hints"),
        (angled, "The description of `see_thing` has `<` or `>`"),
        (controlled, "The `Touches:` line of `paint_thing` has a control character"),
        (listed, "`things` of `sort_things` has a schema the page cannot show"),
        (mixed, "`thing` of `pick_thing` has a schema the page cannot show"),
    ],
)
def test_what_the_page_cannot_show_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys, server: Callable[[], MCPServer], said: str
):
    with pytest.raises(mcp_reference.UnsupportedError, match=said):
        mcp_reference.render(server())

    monkeypatch.setattr(mcp_reference, "create_server", lambda **_: server())

    assert mcp_reference.main(["--check"]) == 2
    assert said in capsys.readouterr().err
