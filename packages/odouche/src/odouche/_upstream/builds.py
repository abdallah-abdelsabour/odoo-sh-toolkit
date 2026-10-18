"""The builds request."""

import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypedDict

from odouche._upstream.reader import Reader
from odouche._upstream.transport import Transport
from odouche.errors import NotFoundError
from odouche.models import Build, BuildResult, BuildStatus, Commit


# What the page asks for, and the most Odoo.sh has been seen to answer.
DEFAULT_LIMIT = 4

_OPERATION = "builds"
_STATUSES = {status.value: status for status in BuildStatus if status is not BuildStatus.UNKNOWN}
_RESULTS = {result.value: result for result in BuildResult if result is not BuildResult.UNKNOWN}
_TIMESTAMP = "%Y-%m-%d %H:%M:%S"
_COMMIT_HASH = re.compile(r"/([0-9a-f]{40}|[0-9a-f]{64})$")

_logger = logging.getLogger("odouche")


def builds(
    transport: Transport, branch_id: int, limit: int = DEFAULT_LIMIT, *, within: float | None = None
) -> list[Build]:
    """List the latest builds of a branch, newest first and at most `limit` of them."""
    listed = [whole(build) for build in _listed(transport, branch_id, limit, within)]
    return sorted(listed, key=lambda build: build.id, reverse=True)[:limit]


def build(transport: Transport, branch_id: int, build_id: int, *, within: float | None = None) -> Build:
    """Return one of the latest builds of a branch. Odoo.sh has no request for a build by its number."""
    number("build", build_id)
    for listed in builds(transport, branch_id, within=within):
        if listed.id == build_id:
            return listed
    raise _not_listed(branch_id, build_id)


def worker(transport: Transport, branch_id: int, build_id: int) -> str | None:
    """Return the address of the worker that holds a build, or `None` while it waits for one."""
    number("build", build_id)
    for listed in _listed(transport, branch_id, DEFAULT_LIMIT):
        if listed.integer("id") == build_id:
            return listed.optional_text("worker_url")
    raise _not_listed(branch_id, build_id)


def _not_listed(branch_id: int, build_id: int) -> NotFoundError:
    return NotFoundError(
        f"Build {build_id} is not among the latest builds of branch {branch_id}.", operation=_OPERATION
    )


def _listed(transport: Transport, branch_id: int, limit: int, within: float | None = None) -> list[Reader]:
    return entry(transport, branch_id, limit, within).items("builds")


def entry(transport: Transport, branch_id: int, limit: int, within: float | None = None) -> Reader:
    """Return what Odoo.sh answers for a branch: its `branch_info` and its latest `builds`."""
    number("branch", branch_id)
    if number("limit", limit) < 1:
        raise ValueError("A limit is at least 1")
    answer = Reader(
        _OPERATION,
        transport.call(
            _OPERATION,
            f"/app/branch/{branch_id}/builds",
            {"build_limit": limit},
            retry=True,
            not_found=f"The session's user can reach no branch numbered {branch_id}. Odoo.sh gives no reason.",
            within=within,
        ),
    )
    branches = answer.items("result")
    if len(branches) != 1:
        raise answer.changed("result")
    return branches[0]


def number(what: str, value: int) -> int:
    """Refuse what is not an integer: a number goes into the address of the request as it is."""
    if type(value) is not int:
        raise TypeError(f"A {what} is given as an integer")
    return value


def whole(build: Reader) -> Build:
    """Read a build from all its fields, as the builds request and the bus give them."""
    branch_id, branch_name = build.pair("branch_id")
    started = build.optional_text("start_datetime")
    return Build(
        id=build.integer("id"),
        name=build.text("name"),
        branch_id=branch_id,
        branch_name=branch_name,
        commit=Commit(
            hash=_commit_hash(build),
            message=build.text("head_commit_msg"),
            author=build.text("head_commit_author"),
            timestamp=timestamp(build, "head_commit_timestamp", build.text("head_commit_timestamp")),
            url=build.text("head_commit_url"),
        ),
        **_outcome(build),
        status_info=build.optional_text("status_info"),
        started_at=None if started is None else timestamp(build, "start_datetime", started),
        url=build.optional_text("url"),
    )


def short(build: Build, values: Reader) -> Build:
    """Return `build` as the short form of a bus event says it is now."""
    info = values.optional_text("status_info") if values.has("status_info") else build.status_info
    return replace(build, **_outcome(values), status_info=info)


class _Outcome(TypedDict):
    status: BuildStatus
    status_name: str
    result: BuildResult | None
    result_name: str | None


def _outcome(build: Reader) -> _Outcome:
    status = build.text("status")
    result = build.optional_text("result")
    return {
        "status": _known(_STATUSES, status, BuildStatus.UNKNOWN, "status"),
        "status_name": status,
        "result": None if result is None else _known(_RESULTS, result, BuildResult.UNKNOWN, "result"),
        "result_name": result,
    }


def _known[T](known: dict[str, T], name: str, unknown: T, kind: str) -> T:
    member = known.get(name)
    if member is None:
        _logger.debug("Unknown build %s: %r", kind, name)
        return unknown
    return member


def timestamp(build: Reader, key: str, value: str) -> datetime:
    try:
        return datetime.strptime(value, _TIMESTAMP).replace(tzinfo=UTC)
    except ValueError:
        pass
    # Raised out of the handler: the exception handled there holds the value.
    raise build.changed(key)


def _commit_hash(build: Reader) -> str:
    found = _COMMIT_HASH.search(build.text("head_commit_url"))
    if found is None:
        raise build.changed("head_commit_url")
    return found.group(1)
