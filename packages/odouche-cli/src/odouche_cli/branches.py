"""`osh branches`: the branches of an Odoo.sh project."""

from typing import Annotated

import typer

import odouche
from odouche_cli._client import open_client
from odouche_cli._context import ProjectOption, checkout_branch, resolve_project, stage_of
from odouche_cli._output import Column, Format, Output


app = typer.Typer(
    name="branches",
    help="List the branches of an Odoo.sh project.",
    epilog="Example: osh branches list --stage staging",
    no_args_is_help=True,
)

_STAGE_ORDER = {odouche.Stage.PRODUCTION: 0, odouche.Stage.STAGING: 1, odouche.Stage.DEVELOPMENT: 2}

StageOption = Annotated[
    list[odouche.Stage] | None,
    typer.Option(
        "--stage",
        help="List only the branches in this stage. Can be given more than once. "
        "`unknown` is any stage `osh` does not know.",
    ),
]


@app.command("list", epilog="Example: osh branches list --stage staging")
def list_(ctx: typer.Context, project: ProjectOption = None, stage: StageOption = None) -> None:
    """List a project's branches with their stage: production, staging, then development, each sorted by name.

    A stage `osh` does not know is shown as Odoo.sh names it, after the others. In the table, `*` marks the branch checked out, when the project comes from the git checkout.
    """
    output: Output = ctx.obj
    with open_client() as client:
        found = client.branches(resolve_project(ctx, project, client.projects))
    if stage:
        found = [branch for branch in found if branch.stage in stage]
    found.sort(key=lambda branch: (_STAGE_ORDER.get(branch.stage, len(_STAGE_ORDER)), branch.name))
    # A project given by name may not be the checkout's.
    current = checkout_branch() if project is None and output.format is Format.TABLE else None
    columns = [
        Column[odouche.Branch]("", lambda branch: "*" if branch.name == current else ""),
        Column[odouche.Branch]("Name", lambda branch: branch.name),
        Column[odouche.Branch]("Stage", stage_of),
    ]
    output.rows(found, columns, empty="No branches.")
