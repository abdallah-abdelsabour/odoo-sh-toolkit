#!/usr/bin/env python3
"""A Claude Code PreToolUse hook that approves the read-only tools of the odouche MCP server.

It prints nothing for any other tool, so Claude Code asks as it would without the hook. It never
denies, never approves a tool that changes state and never reads a call's arguments.
"""

import json
import sys
from typing import TextIO


# The name the server has in the client's configuration: tools are called `mcp__<name>__<tool>`.
SERVER = "odouche"

# A test holds this to the tools the server registers with the read-only hint.
READ_ONLY = frozenset(
    {
        "get_build",
        "get_firewall",
        "get_session",
        "list_branches",
        "list_builds",
        "list_projects",
        "read_log",
        "wait_for_build",
    }
)


def main(stdin: TextIO, stdout: TextIO) -> None:
    """Write an approval when the call on `stdin` is to a read-only tool, and nothing otherwise."""
    try:
        name = json.load(stdin)["tool_name"]
        if name not in {f"mcp__{SERVER}__{tool}" for tool in READ_ONLY}:
            return
        decision = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "a read-only tool of odouche",
        }
        approval = json.dumps({"hookSpecificOutput": decision})
    except Exception:  # noqa: BLE001  No output is a prompt, which is the answer to anything unexpected.
        return
    stdout.write(approval + "\n")


if __name__ == "__main__":
    main(sys.stdin, sys.stdout)
