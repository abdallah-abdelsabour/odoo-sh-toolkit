"""The tools that read projects, branches and builds.

Branch names, commit messages and author names are written by third parties. They are returned in
the fields of the library's models only, never in a sentence the server writes.
"""

from dataclasses import dataclass

from mcp.server.mcpserver.exceptions import ToolError

import odouche
from odouche_mcp._session import open_client


DEFAULT_LISTED = 50
MAX_LISTED = 200
"""The most projects or branches one call returns."""

DEFAULT_BUILDS = 4
MAX_BUILDS = 20
"""The most builds one call returns."""


@dataclass(frozen=True)
class Projects:
    projects: list[odouche.Project]
    truncated: bool


@dataclass(frozen=True)
class Branches:
    branches: list[odouche.Branch]
    truncated: bool


@dataclass(frozen=True)
class Builds:
    builds: list[odouche.Build]
    truncated: bool


@dataclass(frozen=True)
class BuildRead:
    build: odouche.Build


@dataclass(frozen=True)
class FirewallRead:
    firewall: odouche.Firewall


def list_projects(limit: int = DEFAULT_LISTED) -> Projects:
    """List the Odoo.sh projects the user can reach. Call it first: every other tool takes a project by its name."""
    limit = capped(limit, MAX_LISTED)
    with open_client() as client:
        found = client.projects()
    return Projects(found[:limit], truncated=len(found) > limit)


def list_branches(project: str, limit: int = DEFAULT_LISTED) -> Branches:
    """List the branches of a project, given by its name, with the stage each sits in."""
    limit = capped(limit, MAX_LISTED)
    with open_client() as client:
        found = client.branches(project)
    return Branches(found[:limit], truncated=len(found) > limit)


def list_builds(project: str, branch: str, limit: int = DEFAULT_BUILDS) -> Builds:
    """List the latest builds of a branch of a project, newest first, with their status, result and commit."""
    limit = capped(limit, MAX_BUILDS)
    with open_client() as client:
        # One more than asked for tells whether there are more.
        found = client.builds(find_branch(client, project, branch), limit=limit + 1)
    return Builds(found[:limit], truncated=len(found) > limit)


def get_build(project: str, branch: str, build_id: int | None = None) -> BuildRead:
    """Read one build of a branch of a project: the build of that number, or the latest one when none is given."""
    with open_client() as client:
        return BuildRead(find_build(client, project, branch, build_id))


def get_firewall(project: str) -> FirewallRead:
    """Read the firewall of a project: the public IPv4 addresses and ranges that may reach its production and
    staging builds by SSH, and by PostgreSQL on a dedicated server.
    """
    with open_client() as client:
        return FirewallRead(client.firewall(project))


def capped(limit: int, cap: int, name: str = "A limit") -> int:
    if limit < 1:
        msg = f"{name} is at least 1."
        raise ToolError(msg)
    return min(limit, cap)


def find_branch(client: odouche.Client, project: str, name: str) -> odouche.Branch:
    for branch in client.branches(project):
        if branch.name == name:
            return branch
    msg = f"Project {project} has no branch {name}."
    raise odouche.NotFoundError(msg)


def find_build(client: odouche.Client, project: str, branch: str, build_id: int | None) -> odouche.Build:
    """Return the build of that number of a branch, or its latest one when none is given."""
    found = find_branch(client, project, branch)
    if build_id is not None:
        return client.build(found, build_id)
    latest = client.latest_build(found)
    if latest is None:
        msg = f"Branch {branch} of {project} has no build."
        raise odouche.NotFoundError(msg)
    return latest
