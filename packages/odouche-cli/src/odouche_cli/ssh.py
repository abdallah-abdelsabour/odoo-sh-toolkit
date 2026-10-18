"""`osh ssh`: a shell on a build of a branch, through the user's own `ssh`.

`osh` reads no key and holds no connection: its process becomes `ssh`.
"""

import os
import shlex
import shutil
import subprocess
import sys
from typing import Annotated, NoReturn

import typer

import odouche
from odouche_cli._client import open_client
from odouche_cli._context import BranchOption, ProjectOption, find_build, project_branch, resolve_branch
from odouche_cli._errors import EXIT_NO_SSH
from odouche_cli._output import strip_control


EPILOG = "Example: osh ssh --branch staging -- ls"

# What the tests replace: the call that turns this process into `ssh`.
_exec = os.execv

ArgsArgument = Annotated[
    list[str] | None,
    typer.Argument(
        metavar="[SSH_ARGS]...",
        help="What to give `ssh` after the host: its own options, or a command to run on the build. "
        "Everything from the first one on is `ssh`'s. Put `--` before it when it starts with a dash.",
    ),
]


def ssh(
    ctx: typer.Context,
    args: ArgsArgument = None,
    project: ProjectOption = None,
    branch: BranchOption = None,
) -> None:
    """Open a shell on the branch's latest build, with your own `ssh`, its configuration and its keys."""
    name = resolve_branch(ctx, branch)
    with open_client() as client:
        project_name, found = project_branch(ctx, client, project, name)
        target = client.ssh_target(find_build(client, project_name, found, None))
    command = ["ssh", "-l", target.user, target.host, *(args or [])]
    if sys.platform == "win32":
        _refuse("`osh` does not hand over to `ssh` on Windows.", subprocess.list2cmdline(command))
    program = shutil.which("ssh")
    if program is None:
        _refuse("No `ssh` to hand over to.", shlex.join(command))
    # The session is not `ssh`'s to inherit.
    if odouche.SESSION_ENV in os.environ:
        del os.environ[odouche.SESSION_ENV]
    sys.stdout.flush()
    sys.stderr.flush()
    _exec(program, command)


def _refuse(reason: str, command: str) -> NoReturn:
    typer.echo(strip_control(f"{reason} Run it yourself: {command}"), err=True)
    raise typer.Exit(EXIT_NO_SSH)
