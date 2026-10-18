"""`osh firewall`: the IPv4 addresses a project lets reach its production and staging builds by SSH."""

import ipaddress
import urllib.request
from typing import Annotated

import typer

import odouche
from odouche_cli import _confirm
from odouche_cli._client import open_client
from odouche_cli._context import ContextError, ProjectOption, resolve_project
from odouche_cli._errors import EXIT_DECLINED, EXIT_NO_ADDRESS
from odouche_cli._output import Column, Output, strip_control


app = typer.Typer(
    name="firewall",
    help="List and change the IPv4 addresses allowed to reach a project's production and staging builds.",
    epilog="Example: osh firewall allow",
    no_args_is_help=True,
)

# Asked only when no address is given. It answers the address it sees, as plain text.
LOOKUP_URL = "https://api.ipify.org"
_LOOKUP_TIMEOUT = 10.0
_APPLIED = "Odoo.sh can take up to 5 minutes to apply it."


def _addresses(firewall: odouche.Firewall) -> str:
    return ", ".join(firewall.addresses) or "no address"


_COLUMNS = [
    Column[odouche.Firewall]("Project", lambda firewall: firewall.project),
    Column[odouche.Firewall]("Addresses", _addresses),
]

AddressArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="[ADDRESS]",
        help=f"The public IPv4 address, or range in CIDR notation, to allow. Default: your public address, as {LOOKUP_URL} sees it.",
        show_default=False,
    ),
]
AddOption = Annotated[
    bool,
    typer.Option("--add", help="Add the address to the others. Default: it replaces them, and is the only one left."),
]
YesOption = Annotated[
    bool,
    typer.Option(
        "--yes", "-y", help="Change the firewall without asking. Needed when stdin or stderr is not a terminal."
    ),
]


@app.command("list", epilog="Example: osh firewall list --project my-project")
def list_(ctx: typer.Context, project: ProjectOption = None) -> None:
    """List the IPv4 addresses a project's firewall lets reach its production and staging builds."""
    output: Output = ctx.obj
    with open_client() as client:
        found = client.firewall(resolve_project(ctx, project, client.projects))
    output.one(found, _COLUMNS)


@app.command(epilog="Example: osh firewall allow --add")
def allow(
    ctx: typer.Context,
    address: AddressArgument = None,
    project: ProjectOption = None,
    *,
    add: AddOption = False,
    yes: YesOption = False,
) -> None:
    """Change state on Odoo.sh: let an IPv4 address reach a project's production and staging builds by SSH.

    The address is your public one unless given, and replaces the others unless `--add` is given.
    It asks first, unless `--yes` is given. Odoo.sh can take up to 5 minutes to apply a change.
    """
    output: Output = ctx.obj
    if not yes and not (_confirm.stdin_is_terminal() and _confirm.stderr_is_terminal()):
        msg = (
            "Stdin or stderr is not a terminal, so the change cannot be confirmed. "
            "Give --yes to change the firewall without being asked."
        )
        raise ContextError(msg, ctx=ctx)
    if address is None:
        address = public_address()
        if address is None:
            typer.echo(f"Your public IPv4 address was not found at {LOOKUP_URL}. Give it as ADDRESS.", err=True)
            raise typer.Exit(EXIT_NO_ADDRESS)
        typer.echo(f"Your public address: {address}", err=True)
    with open_client(writes=True) as client:
        name = resolve_project(ctx, project, client.projects)
        current = client.firewall(name)
        try:
            wanted = client.plan_allow_ip(current, address, replace=not add)
        except ValueError as error:
            raise typer.BadParameter(str(error), ctx=ctx, param_hint="'[ADDRESS]'") from None
        if wanted == current:
            typer.echo(
                strip_control(f"The firewall of {name} already is {_addresses(current)}. Nothing was sent."), err=True
            )
            output.one(current, _COLUMNS)
            return
        for line in (f"Project: {name}", f"Now: {_addresses(current)}", f"After: {_addresses(wanted)}"):
            typer.echo(strip_control(line), err=True)
        if not yes and not _confirm.confirmed("Change the firewall?"):
            typer.echo("Nothing was sent.", err=True)
            raise typer.Exit(EXIT_DECLINED)
        # Sent once: an error is reported, never retried.
        changed = client.allow_ip(name, address, replace=not add)
    typer.echo(f"The firewall was changed. {_APPLIED}", err=True)
    output.one(changed, _COLUMNS)


def public_address() -> str | None:
    """Return the public IPv4 address this machine reaches the internet from, or `None` when it is not found."""
    try:
        with urllib.request.urlopen(LOOKUP_URL, timeout=_LOOKUP_TIMEOUT) as answer:
            text = answer.read(64).decode("ascii").strip()
        return str(ipaddress.IPv4Address(text))
    except (OSError, ValueError):
        return None
