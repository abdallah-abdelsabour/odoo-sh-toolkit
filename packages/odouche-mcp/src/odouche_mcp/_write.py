"""The tools that change state on Odoo.sh, registered only when the server is started with the switch.

Each call writes one line to stderr, with the tool, its target and what came of it.
"""

import contextlib
import sys
from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import odouche
from odouche_mcp._read import find_branch
from odouche_mcp._session import open_client


# Destructive as the cautious answer: a rebuild was seen once. Not idempotent: a second call starts a second build.
CHANGING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
# Destructive: `replace` removes the other addresses. Idempotent: a second call finds the firewall as asked and sends
# nothing.
FIREWALL = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)


@dataclass(frozen=True)
class Rebuilt:
    build: odouche.Build


@dataclass(frozen=True)
class FirewallChanged:
    firewall: odouche.Firewall
    changed: bool


def changing_tool(*, allow_changes: bool) -> Callable[[str, str], Rebuilt]:
    """Return the rebuild tool, whose client is read-only unless `allow_changes`."""

    def rebuild_branch(project: str, branch: str) -> Rebuilt:
        """Change state on Odoo.sh: start a new build of a branch of a project, which replaces its latest one.
        Only a development or a staging branch is rebuilt.
        """
        try:
            with open_client(changes=allow_changes) as client:
                # Sent once: an error is reported, never retried.
                started = client.rebuild(find_branch(client, project, branch))
        except Exception as error:
            _audit("rebuild_branch", f"project={project!r} branch={branch!r}", type(error).__name__)
            raise
        _audit("rebuild_branch", f"project={project!r} branch={branch!r}", f"build {started.id} started")
        return Rebuilt(started)

    return rebuild_branch


def firewall_tool(*, allow_changes: bool) -> Callable[[str, str, bool], FirewallChanged]:
    """Return the firewall tool, whose client is read-only unless `allow_changes`."""

    def update_firewall(project: str, address: str, replace: bool = False) -> FirewallChanged:  # noqa: FBT001, FBT002
        """Change state on Odoo.sh: let a public IPv4 address, or a range in CIDR notation, reach the production and
        staging builds of a project by SSH. It is added to the others, or with `replace` becomes the only one.
        Give the address to allow: this server does not look one up.
        """
        target = f"project={project!r} address={address!r} replace={replace}"
        try:
            with open_client(changes=allow_changes) as client:
                current = client.firewall(project)
                try:
                    wanted = client.plan_allow_ip(current, address, replace=replace)
                except ValueError as error:
                    raise ToolError(str(error)) from None
                if wanted == current:
                    _audit("update_firewall", target, "unchanged")
                    return FirewallChanged(current, changed=False)
                # Sent once: an error is reported, never retried.
                changed = client.allow_ip(project, address, replace=replace)
        except Exception as error:
            _audit("update_firewall", target, type(error).__name__)
            raise
        _audit("update_firewall", target, "changed")
        return FirewallChanged(changed, changed=True)

    return update_firewall


def _audit(tool: str, target: str, outcome: str) -> None:
    # Not through `logging`, which a level can silence. Names and addresses are third-party text, hence `repr`.
    # A stderr that cannot be written must not make an error of a change that was made.
    with contextlib.suppress(OSError, ValueError):
        print(f"odouche-mcp: {tool} {target}: {outcome}", file=sys.stderr)  # noqa: T201
