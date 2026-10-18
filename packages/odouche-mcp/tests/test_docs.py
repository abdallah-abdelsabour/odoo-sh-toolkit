"""`docs/mcp.md` and the package's README, held to the server and to its version."""

import asyncio
import json
import re
from pathlib import Path

import pytest

import odouche_mcp
from odouche_mcp.server import create_server


ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "mcp.md"
README = ROOT / "packages" / "odouche-mcp" / "README.md"
RELEASE = ROOT / "release-please-config.json"
PINNED = [GUIDE, README]

_SECTION = re.compile(r"^### `(\w+)`\n(.*?)(?=^##)", re.MULTILINE | re.DOTALL)
_COMMAND = re.compile(r"odouche-mcp(?![\w-])(==[\w.]+)?")
# What Release Please rewrites a version between.
_REWRITTEN = re.compile(r"<!-- x-release-please-start-version -->\n(.*?)<!-- x-release-please-end -->", re.DOTALL)
_BLOCK = re.compile(r"^```\w*\n(.*?)^```", re.MULTILINE | re.DOTALL)
# The server started with no version, as a command or as an argument of one.
_UNPINNED = re.compile(r'uvx odouche-mcp(?!==)|"odouche-mcp"')


def sections() -> dict[str, str]:
    """Return the text under each heading of the guide that is a tool's name."""
    return dict(_SECTION.findall(GUIDE.read_text(encoding="utf-8")))


def test_the_guide_has_a_section_for_every_tool_and_no_other():
    registered = [tool.name for tool in asyncio.run(create_server(allow_changes=True).list_tools())]

    assert list(sections()) == registered


@pytest.mark.parametrize("page", PINNED, ids=lambda page: page.name)
def test_every_command_that_starts_the_server_pins_its_version(page: Path):
    text = page.read_text(encoding="utf-8")
    blocks = "".join(_BLOCK.findall(text))
    pins = [match.group(1) for match in _COMMAND.finditer(blocks)]

    assert pins
    assert not _UNPINNED.search(text)
    assert set(pins) == {f"=={odouche_mcp.__version__}"}


@pytest.mark.parametrize("page", PINNED, ids=lambda page: page.name)
def test_release_please_rewrites_every_pinned_version(page: Path):
    text = page.read_text(encoding="utf-8")
    files = json.loads(RELEASE.read_text(encoding="utf-8"))["packages"]["."]["extra-files"]

    assert page.relative_to(ROOT).as_posix() in files
    assert "==" not in _REWRITTEN.sub("", text)
