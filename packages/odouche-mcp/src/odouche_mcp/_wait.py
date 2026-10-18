"""The tool that waits for a build to finish, for a bounded time.

A call is one request and one answer, and a client gives up on a long one: the wait is capped, and
a build that outlasts it is an answer, which says to call again.
"""

import re
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from typing import Any

import anyio.from_thread
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

import odouche
from odouche_mcp._read import capped, find_branch, find_build
from odouche_mcp._session import open_client


DEFAULT_WAIT = 30
MAX_WAIT = 50
"""The longest one call waits, in seconds: under the 60 after which common clients give a call up."""

# How long the watch says nothing before the tool looks whether the client gave the call up.
_PULSE = 2.0
# How long between two requests for a build of the awaited commit.
_POLL = 3.0
_COMMIT = re.compile(r"[0-9a-f]{7,64}", re.IGNORECASE)

STILL_RUNNING = "The build has not finished, which is not a failure: call the tool again to keep waiting."
NOT_LISTED = (
    "The branch has no build of that commit yet: call the tool again to keep waiting. "
    "If it never comes, the commit was not pushed to that branch."
)

# What the tests replace: the clock of the deadline and the wait between two requests.
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class BuildWait:
    build: odouche.Build | None
    finished: bool
    timeout: int
    timeout_capped: bool
    next_step: str | None


def wait_for_build(
    project: str,
    branch: str,
    ctx: Context[Any, Any],
    *,
    build_id: int | None = None,
    commit: str | None = None,
    timeout: int = DEFAULT_WAIT,
) -> BuildWait:
    """Wait for a build of a branch of a project to finish, for `timeout` seconds at most: the build of that
    number, or the latest one. With `commit`, the first 7 to 64 digits of a hash, the build of that commit, once
    the branch has one: give it right after a push, when the latest build is still the previous one. A build
    that has not finished in time is not an error: `finished` is false, and `next_step` says to call again.
    """
    applied = capped(timeout, MAX_WAIT, "`timeout`")
    if commit is not None and build_id is not None:
        msg = "A build is given by `build_id` or by `commit`, not by both."
        raise ToolError(msg)
    if commit is not None and not _COMMIT.fullmatch(commit):
        msg = "A commit is 7 to 64 hexadecimal digits."
        raise ToolError(msg)
    deadline = _monotonic() + applied

    def answer(build: odouche.Build | None, step: str | None) -> BuildWait:
        finished = build is not None and build.finished
        return BuildWait(build, finished, applied, timeout_capped=applied < timeout, next_step=step)

    with open_client() as client:
        if commit is None:
            build = find_build(client, project, branch, build_id)
        else:
            build = _awaited(client, find_branch(client, project, branch), commit.lower(), deadline)
            if build is None:
                return answer(None, NOT_LISTED)
        remaining = deadline - _monotonic()
        if not build.finished and remaining > 0:
            build = _watched(client, ctx, project, build, remaining)
    return answer(build, None if build.finished else STILL_RUNNING)


def _awaited(client: odouche.Client, branch: odouche.Branch, commit: str, deadline: float) -> odouche.Build | None:
    """Return the branch's build of the commit that hash begins, or `None` when the deadline passes with none."""
    while True:
        for build in client.builds(branch):
            if build.commit.hash.lower().startswith(commit):
                return build
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return None
        _sleep(min(_POLL, remaining))
        anyio.from_thread.check_cancelled()


def _watched(
    client: odouche.Client, ctx: Context[Any, Any], project: str, build: odouche.Build, timeout: float
) -> odouche.Build:
    """Watch a build until it finishes or the timeout, and return it as last seen."""
    changes = 0
    try:
        with closing(client.watch_build(project, build, timeout=timeout, pulse=_PULSE)) as watching:
            for seen in watching:
                # Raises once the client has given the call up, which closes the watch.
                anyio.from_thread.check_cancelled()
                if _state(seen) != _state(build):
                    changes += 1
                    anyio.from_thread.run(ctx.report_progress, changes, None, _progress(seen))
                build = seen
    except odouche.StreamTimeoutError:
        pass
    return build


def _state(build: odouche.Build) -> tuple[str, str | None, str | None]:
    return build.status_name, build.result_name, build.status_info


def _progress(build: odouche.Build) -> str:
    """Say where a build is, in the library's words only: what Odoo.sh says of it stays in the result."""
    line = f"Build {build.id}: {build.status.value}"
    return f"{line}, {build.result.value}" if build.result else line
