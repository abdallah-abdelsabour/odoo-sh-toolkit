"""`osh projects`: the Odoo.sh projects the session's user can reach."""

import typer

import odouche
from odouche_cli._client import open_client
from odouche_cli._output import Column, Output


app = typer.Typer(
    name="projects",
    help="List the Odoo.sh projects you can reach.",
    epilog="Example: osh projects list",
    no_args_is_help=True,
)

_COLUMNS = [
    Column[odouche.Project]("Name", lambda project: project.name),
    Column[odouche.Project]("Repository", lambda project: project.repository),
    Column[odouche.Project]("URL", lambda project: project.url),
]


@app.command("list", epilog="Example: osh projects list")
def list_(ctx: typer.Context) -> None:
    """List the projects you can reach, with their repository and address."""
    output: Output = ctx.obj
    with open_client() as client:
        found = client.projects()
    output.rows(found, _COLUMNS, empty="No projects.")
