"""The Claude Code hook in `examples/`, held to the server and to the guide."""

import asyncio
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from odouche_mcp.server import create_server


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "examples" / "claude-code" / "odouche_read_only_hook.py"
GUIDE = ROOT / "docs" / "mcp.md"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("odouche_read_only_hook", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load()


def run(text: str) -> str:
    stdout = io.StringIO()
    hook.main(io.StringIO(text), stdout)
    return stdout.getvalue()


def call(name: object) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": {"project": "acme"}})


def test_the_list_is_the_read_only_tools_of_the_server():
    tools = asyncio.run(create_server(allow_changes=True).list_tools())

    assert {tool.name for tool in tools if tool.annotations and tool.annotations.read_only_hint} == hook.READ_ONLY


@pytest.mark.parametrize("tool", sorted(hook.READ_ONLY))
def test_a_read_only_tool_is_approved(tool: str):
    assert json.loads(run(call(f"mcp__odouche__{tool}"))) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "a read-only tool of odouche",
        }
    }


@pytest.mark.parametrize(
    "text",
    [
        call("mcp__odouche__rebuild_branch"),
        call("mcp__odouche__delete_project"),
        call("mcp__other__list_builds"),
        call("mcp__odouche__list_builds_"),
        call("xmcp__odouche__list_builds"),
        call("list_builds"),
        call("Bash"),
        call(None),
        call(["mcp__odouche__list_builds"]),
        call({"mcp__odouche__list_builds": 1}),
        json.dumps({"tool_input": {"tool_name": "mcp__odouche__list_builds"}}),
        json.dumps(["mcp__odouche__list_builds"]),
        json.dumps("mcp__odouche__list_builds"),
        '{"tool_name": "mcp__odouche__list_builds"',
        "not json",
        "",
    ],
)
def test_anything_else_prints_nothing(text: str):
    assert not run(text)


def test_a_stream_that_cannot_be_read_prints_nothing():
    class Broken(io.StringIO):
        def read(self, size: int | None = -1) -> str:
            raise OSError(size)

    stdout = io.StringIO()
    hook.main(Broken(), stdout)

    assert not stdout.getvalue()


@pytest.mark.parametrize(
    ("tool", "approved"), [("list_builds", True), ("rebuild_branch", False)], ids=["read", "change"]
)
def test_the_script_runs_on_the_standard_library_alone(*, tool: str, approved: bool):
    done = subprocess.run(  # noqa: S603  The interpreter running the tests, on a file of the repository.
        [sys.executable, "-I", "-S", str(SCRIPT)],
        input=call(f"mcp__odouche__{tool}"),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert bool(done.stdout) is approved
    assert not done.stderr


def test_the_guide_sends_every_tool_of_the_server_to_the_hook():
    section = GUIDE.read_text(encoding="utf-8").split("\n### Asking before a change\n", 1)[1].split("\n##", 1)[0]
    snippet = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    (rule,) = snippet["hooks"]["PreToolUse"]

    for tool in asyncio.run(create_server(allow_changes=True).list_tools()):
        assert re.fullmatch(rule["matcher"], f"mcp__odouche__{tool.name}")
    assert not re.fullmatch(rule["matcher"], "mcp__other__list_builds")
    assert SCRIPT.name in rule["hooks"][0]["command"]
    assert SCRIPT.relative_to(ROOT).as_posix() in section
