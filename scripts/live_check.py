"""Check the library's read path against the real Odoo.sh, with the session of whoever runs it.

The output holds shapes and counts only, so that it can be pasted into a public issue.
"""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from odouche import (
    Branch,
    Build,
    BuildResult,
    BuildStatus,
    Client,
    Log,
    LogKind,
    OdoucheError,
    Stage,
    UpstreamChangedError,
)


OK = "pass"
CHANGED = "changed"
ERROR = "error"
SKIPPED = "skipped"

_FAILED = 1
_REFUSED = 2

_TAIL = 20


@dataclass(frozen=True, slots=True)
class Step:
    """What one request of the read path came to."""

    name: str
    outcome: str
    detail: str = ""


def run(client: Client, project: str) -> list[Step]:
    """Walk the read path of one project, past any step that fails."""
    steps: list[Step] = []
    _attempt(steps, "identity", client.identity)
    _attempt(steps, "projects", client.projects, lambda found: _count(len(found), "project"))

    branches = _attempt(steps, "branches", lambda: client.branches(project), _branches)
    branch = _pick(branches) if branches else None
    builds = (
        _attempt(steps, "builds", lambda: client.builds(branch), _builds)
        if branch
        else _skip(steps, "builds", "a branch")
    )
    latest = builds[0] if builds else None
    build = (
        _attempt(steps, "build", lambda: client.build(latest.branch_id, latest.id))
        if latest
        else _skip(steps, "build", "a build")
    )
    logs = (
        _attempt(steps, "logs", lambda: client.logs(project, build), _logs)
        if build
        else _skip(steps, "logs", "a build")
    )
    if build and logs:
        _attempt(
            steps,
            "log tail",
            lambda: list(client.read_log(project, build, logs[0].name, tail=_TAIL)),
            lambda lines: _count(len(lines), "line"),
        )
    else:
        _skip(steps, "log tail", "a log")
    return steps


def render(steps: Sequence[Step]) -> str:
    """Return the report: a line per step, then the tally."""
    width = max(len(step.name) for step in steps)
    lines = [f"{step.name:<{width}}  {step.outcome:<{len(SKIPPED)}}  {step.detail}".rstrip() for step in steps]
    tally = Counter(step.outcome for step in steps)
    lines.append(f"{tally[OK]} passed, {tally[CHANGED] + tally[ERROR]} failed, {tally[SKIPPED]} skipped")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the check and print its report. Refuses in CI, before anything is contacted."""
    if "CI" in os.environ:
        sys.stderr.write("Refused: the live check never runs in CI.\n")
        return _REFUSED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="the name of the project to check, as in the address of its page")
    arguments = parser.parse_args(argv)
    try:
        client = Client(read_only=True)
    except OdoucheError as error:
        sys.stderr.write(f"No session to check with: {type(error).__name__}.\n")
        return _FAILED
    with client:
        steps = run(client, arguments.project)
    sys.stdout.write(render(steps))
    return _FAILED if any(step.outcome in {CHANGED, ERROR} for step in steps) else 0


def _attempt[T](
    steps: list[Step], name: str, call: Callable[[], T], describe: Callable[[T], str] = lambda _: ""
) -> T | None:
    try:
        value = call()
    except OdoucheError as error:
        steps.append(_failure(name, error))
        return None
    steps.append(Step(name, OK, describe(value)))
    return value


def _skip(steps: list[Step], name: str, needs: str) -> None:
    steps.append(Step(name, SKIPPED, f"needs {needs}"))


# Never the message: it can quote a project, a branch or a build.
def _failure(name: str, error: OdoucheError) -> Step:
    if isinstance(error, UpstreamChangedError):
        return Step(name, CHANGED, f"{error.operation} at {error.field}")
    status = f" {error.status}" if error.status else ""
    return Step(name, ERROR, f"{type(error).__name__}{status}")


def _pick(branches: Sequence[Branch]) -> Branch:
    return next((branch for branch in branches if branch.stage is Stage.PRODUCTION), branches[0])


def _count(number: int, noun: str, plural: str | None = None) -> str:
    return f"{number} {noun if number == 1 else plural or noun + 's'}"


def _unknown(number: int, noun: str) -> str:
    return f", {number} of an unknown {noun}" if number else ""


def _branches(branches: Sequence[Branch]) -> str:
    unknown = sum(branch.stage is Stage.UNKNOWN for branch in branches)
    return _count(len(branches), "branch", "branches") + _unknown(unknown, "stage")


def _builds(builds: Sequence[Build]) -> str:
    unknown = sum(build.status is BuildStatus.UNKNOWN or build.result is BuildResult.UNKNOWN for build in builds)
    return _count(len(builds), "build") + _unknown(unknown, "status or result")


def _logs(logs: Sequence[Log]) -> str:
    unknown = sum(log.kind is LogKind.UNKNOWN for log in logs)
    return _count(len(logs), "log") + _unknown(unknown, "kind")


if __name__ == "__main__":
    sys.exit(main())
