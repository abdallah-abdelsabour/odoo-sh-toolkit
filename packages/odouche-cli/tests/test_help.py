from collections.abc import Iterator
from typing import Annotated, Any

import typer

from odouche_cli.app import app


def unhelped(command: Any, path: str) -> Iterator[str]:
    """Walk a command's tree and name each command, group, argument and option that has no help text."""
    if not (command.help or "").strip():
        yield path
    for parameter in command.params:
        if not (getattr(parameter, "help", None) or "").strip():
            yield f"{path} {parameter.opts[0]}"
    children: dict[str, Any] = getattr(command, "commands", {})
    for name, child in children.items():
        yield from unhelped(child, f"{path} {name}")


def test_every_command_and_option_has_help():
    # Typer's completion options and `--help` are in the tree too, with the text they come with.
    assert list(unhelped(typer.main.get_command(app), "osh")) == []


def test_every_command_has_an_example():
    def without(command: Any, path: str) -> Iterator[str]:
        if not (command.epilog or "").startswith("Example: "):
            yield path
        children: dict[str, Any] = getattr(command, "commands", {})
        for name, child in children.items():
            yield from without(child, f"{path} {name}")

    # The root's help is the list of commands.
    assert list(without(typer.main.get_command(app), "osh")) == ["osh"]


def test_a_tree_without_help_is_named_part_by_part():
    silent = typer.Typer(name="silent")
    group = typer.Typer(name="group")
    silent.add_typer(group)

    @silent.callback()
    def root() -> None:  # pyright: ignore[reportUnusedFunction]
        pass

    @group.command()
    def told(  # pyright: ignore[reportUnusedFunction]
        name: Annotated[str, typer.Argument(help="A name.")],
        *,
        loud: Annotated[bool, typer.Option(help="Loud.")] = False,
    ) -> None:
        """Told."""
        typer.echo((name, loud))

    @group.command()
    def untold(name: str, *, loud: bool = False) -> None:  # pyright: ignore[reportUnusedFunction]
        typer.echo((name, loud))

    found = list(unhelped(typer.main.get_command(silent), "silent"))

    assert found == [
        "silent",
        "silent group",
        "silent group untold",
        "silent group untold name",
        "silent group untold --loud",
    ]
