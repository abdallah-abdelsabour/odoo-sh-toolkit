"""`osh logs`: a log of a build of a branch of an Odoo.sh project.

A log is untrusted text. None of it goes anywhere but stdout.
"""

import math
from collections.abc import Generator
from contextlib import closing
from typing import Annotated

import typer

import odouche
from odouche_cli._client import open_client
from odouche_cli._context import BranchOption, ProjectOption, find_build, is_given, project_branch, resolve_branch
from odouche_cli._output import Column, Output
from odouche_cli._time import ago


EPILOG = "Example: osh logs --follow"

# Without `--kind`, the first of these the build has.
_DEFAULT_KINDS = (odouche.LogKind.INSTALL.value, odouche.LogKind.ODOO.value)
_DEFAULT_TAIL = 100

# The options that print a log, by parameter, which `--kinds` does not go with.
_PRINTING = {
    "kind": "--kind",
    "tail": "--tail",
    "everything": "--all",
    "follow": "--follow",
    "strip": "--strip or --no-strip",
    "timeout": "--timeout",
}

BuildOption = Annotated[
    int | None,
    typer.Option(
        "--build",
        metavar="BUILD_ID",
        min=1,
        help="The build's number, among the branch's latest builds. Default: the latest one.",
    ),
]
KindOption = Annotated[
    str | None,
    typer.Option(
        "--kind",
        metavar="NAME",
        help="Which log to print, by its name. `--kinds` lists them. "
        "Default: `install` when the build has it, `odoo` otherwise.",
    ),
]
KindsOption = Annotated[
    bool,
    typer.Option(
        "--kinds",
        help="List the logs the build has, and print none. Goes with none of the options that print a log.",
    ),
]
TailOption = Annotated[
    int,
    typer.Option(
        "--tail",
        min=1,
        metavar="LINES",
        help="Print the last lines, out of the log's last mebibyte: a large tail can print fewer.",
    ),
]
AllOption = Annotated[
    bool,
    typer.Option("--all", help="Print the whole log. Does not go with `--tail`."),
]
FollowOption = Annotated[
    bool,
    typer.Option(
        "--follow",
        "-f",
        help="Keep printing the lines as they are written, until interrupted. With `--all`, from the first line.",
    ),
]
StripOption = Annotated[
    bool | None,
    typer.Option(
        "--strip/--no-strip",
        help="Remove the escape sequences and control characters from the lines, or leave them. "
        "Default: remove them when stdout is a terminal.",
    ),
]
TimeoutOption = Annotated[
    int | None,
    typer.Option(
        "--timeout",
        min=1,
        metavar="SECONDS",
        help="The longest to follow the log: the command then exits 8. Goes with `--follow` only. Default: no limit.",
    ),
]


def logs(
    ctx: typer.Context,
    project: ProjectOption = None,
    branch: BranchOption = None,
    *,
    build_id: BuildOption = None,
    kind: KindOption = None,
    kinds: KindsOption = False,
    tail: TailOption = _DEFAULT_TAIL,
    everything: AllOption = False,
    follow: FollowOption = False,
    strip: StripOption = None,
    timeout: TimeoutOption = None,
) -> None:
    """Print the end of a build's log: the install or odoo log of the branch's latest build, or another."""
    output: Output = ctx.obj
    _refuse_conflicts(ctx)
    name = resolve_branch(ctx, branch)
    with open_client() as client:
        project_name, found = project_branch(ctx, client, project, name)
        build = find_build(client, project_name, found, build_id)
        if kinds:
            columns = [
                Column[odouche.Log]("Kind", lambda log: log.kind.value),
                Column[odouche.Log]("Name", lambda log: log.name),
                Column[odouche.Log]("Size", lambda log: log.size),
                Column[odouche.Log]("Changed", lambda log: ago(log.modified_at)),
            ]
            output.rows(client.logs(project_name, build), columns, empty=f"Build {build.id} has no log yet.")
            return
        limit = math.inf if timeout is None else timeout

        def start(kind: str) -> Generator[odouche.LogLine]:
            if not follow:
                return client.read_log(project_name, build, kind, tail=None if everything else tail)
            if everything:
                return client.follow_log(project_name, build, kind, timeout=limit, offset=0)
            return client.follow_log(project_name, build, kind, timeout=limit, tail=tail)

        wanted = _DEFAULT_KINDS if kind is None else (kind,)
        try:
            lines = start(wanted[0])
        except odouche.NotFoundError:
            names = [log.name for log in client.logs(project_name, build)]
            if wanted[0] in names:
                raise
            fallback = next((name for name in wanted[1:] if name in names), None)
            if fallback is None:
                raise _absent(build, wanted, names) from None
            lines = start(fallback)
        with closing(lines):
            output.lines(lines, lambda line: line.text, strip=strip)


def _absent(build: odouche.Build, kinds: tuple[str, ...], names: list[str]) -> odouche.NotFoundError:
    """Make the error for a build that has none of `kinds`, which names the logs it has."""
    if not names:
        return odouche.NotFoundError(f"Build {build.id} has no log yet.")
    return odouche.NotFoundError(f"Build {build.id} has no {' or '.join(kinds)} log. It has: {', '.join(names)}.")


def _refuse_conflicts(ctx: typer.Context) -> None:
    """Fail on options that do not go together, as a usage error."""
    given = {parameter for parameter in _PRINTING if is_given(ctx, parameter)}
    msg = None
    if ctx.params["kinds"] and given:
        msg = f"--kinds does not go with {', '.join(flag for parameter, flag in _PRINTING.items() if parameter in given)}."
    elif {"tail", "everything"} <= given:
        msg = "--all does not go with --tail."
    elif "timeout" in given and "follow" not in given:
        msg = "--timeout goes with --follow only."
    if msg is not None:
        raise typer.BadParameter(msg, ctx=ctx)
