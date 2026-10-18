"""The MCP server and its entry point."""

import argparse
from collections.abc import Sequence

from mcp.server import MCPServer

from odouche_mcp import __version__
from odouche_mcp._contract import add_tool
from odouche_mcp._logs import DEFAULT_LINES, MAX_BYTES, MAX_LINES, read_log
from odouche_mcp._read import (
    DEFAULT_BUILDS,
    DEFAULT_LISTED,
    MAX_BUILDS,
    MAX_LISTED,
    get_build,
    get_firewall,
    list_branches,
    list_builds,
    list_projects,
)
from odouche_mcp._session import get_session
from odouche_mcp._wait import DEFAULT_WAIT, MAX_WAIT, wait_for_build
from odouche_mcp._write import CHANGING, FIREWALL, changing_tool, firewall_tool


def create_server(*, allow_changes: bool = False) -> MCPServer:
    """Build the server. Tools are registered here, through `_contract.add_tool`.

    The tools that change state on Odoo.sh are registered only with `allow_changes`.
    """
    server = MCPServer(name="odouche", version=__version__)
    add_tool(
        server,
        get_session,
        returns="whether a session is available, its source, its user and the seconds left before its max age.",
        bounds="one request to Odoo.sh, none when there is no session.",
    )
    listed = f"`limit` items, {DEFAULT_LISTED} by default and {MAX_LISTED} at most"
    add_tool(
        server,
        list_projects,
        returns="the projects, each with its name, repository and address, and `truncated`, true when there are more.",
        bounds=f"one request to Odoo.sh; {listed}.",
    )
    add_tool(
        server,
        list_branches,
        returns="the branches, each with its name and stage, and `truncated`, true when there are more.",
        bounds=f"two requests to Odoo.sh; {listed}.",
    )
    add_tool(
        server,
        list_builds,
        returns="the builds, and `truncated`, true when Odoo.sh answered more than `limit`.",
        bounds=(
            f"three requests to Odoo.sh; `limit` builds, {DEFAULT_BUILDS} by default and {MAX_BUILDS} at most. "
            "Odoo.sh has only been seen to answer 4: older builds are out of reach, whatever `truncated` says."
        ),
    )
    add_tool(
        server,
        get_build,
        returns="the build, with its commit and the address of its database.",
        bounds="three requests to Odoo.sh; a build older than the branch's latest ones is not found.",
    )
    add_tool(
        server,
        wait_for_build,
        returns=(
            "the build as last seen, or none while the branch has no build of `commit`, `finished`, the `timeout` "
            "applied, `timeout_capped`, true when it was lowered, and `next_step`, set while the build has not finished."
        ),
        bounds=(
            f"`timeout` seconds, {DEFAULT_WAIT} by default and {MAX_WAIT} at most, longer when Odoo.sh is slow to "
            "answer for the branch or for a commit's build; six requests to Odoo.sh and one socket, more when the "
            "socket drops, and one request every 3 seconds while a commit has no build."
        ),
    )
    add_tool(
        server,
        read_log,
        returns=(
            "the build's number, the log's kind, `untrusted_lines`, which holds the lines without their control "
            "characters, and `truncated`, true when what was read held more lines or one was cut."
        ),
        bounds=(
            "seven requests to Odoo.sh and the build's worker, ten when no `kind` is given; "
            f"`lines` lines, {DEFAULT_LINES} by default and {MAX_LINES} at most, out of the log's "
            f"last mebibyte, which is all that is read, and {MAX_BYTES} bytes of lines at most, as JSON."
        ),
    )
    add_tool(
        server,
        get_firewall,
        returns="the firewall: the project's name and its `addresses`, public IPv4 addresses and CIDR ranges.",
        bounds="two requests to Odoo.sh; a firewall holds 10 entries at most.",
    )
    if allow_changes:
        add_tool(
            server,
            changing_tool(allow_changes=allow_changes),
            returns="the new build, which is in progress.",
            touches="starts a new build of the branch, which replaces its latest one.",
            bounds="five requests to Odoo.sh; the rebuild is sent once and never retried.",
            annotations=CHANGING,
        )
        add_tool(
            server,
            firewall_tool(allow_changes=allow_changes),
            returns="the firewall as Odoo.sh stored it, and `changed`, false when it already was as asked.",
            touches=(
                "the project's firewall, which lets addresses reach its production and staging builds; "
                "with `replace`, the other addresses are removed."
            ),
            bounds=(
                "three requests to Odoo.sh, two when nothing changes; the change is sent once and never retried, "
                "and Odoo.sh can take up to 5 minutes to apply it."
            ),
            annotations=FIREWALL,
        )
    return server


def main(argv: Sequence[str] | None = None) -> None:
    """Run the server over stdio."""
    parser = argparse.ArgumentParser(
        prog="odouche-mcp", description="An unofficial MCP server for Odoo.sh.", allow_abbrev=False
    )
    parser.add_argument(
        "--allow-changes",
        action="store_true",
        help="also register `rebuild_branch` and `update_firewall`, the tools that change state on Odoo.sh",
    )
    options = parser.parse_args(argv)
    create_server(allow_changes=options.allow_changes).run(transport="stdio")
