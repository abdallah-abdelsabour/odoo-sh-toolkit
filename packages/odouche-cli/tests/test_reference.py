from collections.abc import Callable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import pytest
import typer

import cli_reference
from odouche_cli.app import app


class Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


def sample() -> typer.Typer:
    """Make an application with one of each thing the page shows."""
    application = typer.Typer(name="tool", help="A tool.")
    group = typer.Typer(name="things", help="Work with things.", epilog="Example: tool things paint 3")
    application.add_typer(group)

    @group.command(epilog="Example: tool things paint 3 --colour blue")
    def paint(  # pyright: ignore[reportUnusedFunction]
        thing: Annotated[int, typer.Argument(metavar="THING_ID", min=1, max=9, help="The thing's number.")],
        *,
        colour: Annotated[
            Colour, typer.Option("--colour", "-c", envvar="TOOL_COLOUR", help="Its colour.")
        ] = Colour.RED,
        coats: Annotated[int, typer.Option(min=1, help="How many coats | layers.")] = 2,
        dry: Annotated[bool, typer.Option("--dry/--wet", help="Let it dry.")] = True,
        secret: Annotated[str | None, typer.Option(hidden=True, help="Not shown.")] = None,
    ) -> None:
        """Paint a thing.

        It dries overnight.
        """
        typer.echo((thing, colour, coats, dry, secret))

    @group.command(hidden=True, help="Not shown.")
    def scrape() -> None:  # pyright: ignore[reportUnusedFunction]
        pass

    return application


def probe(command: Callable[..., None]) -> str:
    """Return the page of an application whose one command is `tool probe`."""
    application = typer.Typer(name="tool", help="A tool.")
    application.callback()(lambda: None)
    application.command("probe", help="Probe.")(command)
    return cli_reference.render(application)


def paths(command: Any, path: str) -> Iterator[str]:
    """Walk a command's tree and yield the path of each command and group."""
    yield path
    children: dict[str, Any] = getattr(command, "commands", {})
    for name, child in children.items():
        if not child.hidden:
            yield from paths(child, f"{path} {name}")


def test_the_committed_page_is_what_the_commands_give():
    committed = cli_reference.PAGE.read_text(encoding="utf-8")

    assert committed == cli_reference.render(), (
        f"docs/{cli_reference.PAGE.name} is stale. Run `{cli_reference.REGENERATE}`."
    )


@pytest.mark.parametrize("variables", [{"COLUMNS": "20", "NO_COLOR": "1"}, {"FORCE_COLOR": "1", "TERM": "dumb"}])
def test_the_page_does_not_depend_on_the_terminal(monkeypatch: pytest.MonkeyPatch, variables: dict[str, str]):
    page = cli_reference.render()
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    assert cli_reference.render() == page


def test_a_new_command_makes_the_committed_page_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    page = tmp_path / "cli-reference.md"
    page.write_text(cli_reference.render(), encoding="utf-8")
    monkeypatch.setattr(cli_reference, "PAGE", page)
    assert cli_reference.main(["--check"]) == 0

    monkeypatch.setattr(cli_reference, "app", sample())

    assert cli_reference.main(["--check"]) == 1
    assert cli_reference.REGENERATE in capsys.readouterr().err
    assert "tool things paint" not in page.read_text(encoding="utf-8")
    assert cli_reference.main([]) == 0
    assert "### `tool things paint`" in page.read_text(encoding="utf-8")
    assert cli_reference.main(["--check"]) == 0


def test_a_missing_page_is_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cli_reference, "PAGE", tmp_path / "cli-reference.md")

    assert cli_reference.main(["--check"]) == 1


def test_a_command_is_rendered_with_its_usage_help_parameters_and_example():
    page = cli_reference.render(sample())

    assert (
        """\
### `tool things paint`

Paint a thing.

It dries overnight.

```text
tool things paint [OPTIONS] {THING_ID}
```

| Argument | Description |
| --- | --- |
| `THING_ID` | The thing's number. Required. From 1 to 9. |

| Option | Description | Environment |
| --- | --- | --- |
| `--colour COLOUR`, `-c COLOUR` | Its colour. One of `red`, `blue`. Default: `red`. | `TOOL_COLOUR` |
| `--coats COATS` | How many coats \\| layers. At least 1. Default: `2`. | |
| `--dry`, `--wet` | Let it dry. Default: `--dry`. | |

Example:

```bash
tool things paint 3 --colour blue
```
"""
        in page
    )


def test_a_group_lists_its_commands_and_links_to_them():
    page = cli_reference.render(sample())

    assert "| [`tool things paint`](#tool-things-paint) | Paint a thing. |" in page
    assert "| [`tool things`](#tool-things) | Work with things. |" in page


def test_what_is_hidden_and_the_help_option_are_left_out():
    page = cli_reference.render(sample())

    assert "scrape" not in page
    assert "--secret" not in page
    assert "| `--help`" not in page


def test_a_group_leaves_its_example_to_its_command():
    page = cli_reference.render(sample())

    assert "tool things paint 3 --colour blue" in page
    assert "tool things paint 3\n" not in page


def test_an_epilog_that_is_no_example_is_kept_for_a_group():
    application = typer.Typer(name="tool", help="A tool.")
    group = typer.Typer(name="things", help="Work with things.", epilog="Things are painted.")
    application.add_typer(group)
    group.command("paint", help="Paint.")(lambda: None)

    assert "\n\nThings are painted.\n\n" in cli_reference.render(application)


def test_a_default_the_help_states_is_not_said_twice():
    def command(
        *,
        coats: Annotated[int, typer.Option(help="How many coats. Default: 2, which covers.")] = 2,
        wet: Annotated[bool, typer.Option("--dry/--wet", help="Let it dry.")] = False,
        quiet: Annotated[bool, typer.Option("--quiet", help="Say nothing.")] = False,
    ) -> None:
        typer.echo((coats, wet, quiet))

    page = probe(command)

    assert "| `--coats COATS` | How many coats. Default: 2, which covers. | |" in page
    assert "| `--dry`, `--wet` | Let it dry. Default: `--wet`. | |" in page
    assert "| `--quiet` | Say nothing. | |" in page


def test_a_count_is_a_flag_with_no_value_and_no_default():
    def command(*, verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="Say more.")] = 0) -> None:
        typer.echo(verbose)

    assert "| `--verbose`, `-v` | Say more. | |" in probe(command)


def test_a_hidden_argument_is_left_out_with_its_table():
    def command(thing: Annotated[str | None, typer.Argument(hidden=True, help="Not shown.")] = None) -> None:
        typer.echo(thing)

    page = probe(command)

    assert "## `tool probe`" in page
    assert "Argument" not in page
    assert "Not shown." not in page


def argument_with_a_default(thing: Annotated[str, typer.Argument(help="A thing.")] = "one") -> None:
    typer.echo(thing)


def argument_with_a_variable(
    thing: Annotated[str | None, typer.Argument(envvar="THING", help="A thing.")] = None,
) -> None:
    typer.echo(thing)


def empty_default(*, thing: Annotated[str, typer.Option(help="A thing.")] = "") -> None:
    typer.echo(thing)


def marker_in_help(*, thing: Annotated[str | None, typer.Option(help="A thing.\n\n\b\nKept as it is.")] = None) -> None:
    typer.echo(thing)


def control_character_in_help(*, thing: Annotated[str | None, typer.Option(help="A \x1b[1mthing.")] = None) -> None:
    typer.echo(thing)


def html_in_help(*, thing: Annotated[str | None, typer.Option(help="A thing, as <name>.")] = None) -> None:
    typer.echo(thing)


def several_defaults(*, thing: Annotated[list[str], typer.Option(help="A thing.")] = ["one"]) -> None:  # noqa: B006
    typer.echo(thing)


def default_not_shown(*, thing: Annotated[str, typer.Option(show_default=False, help="A thing.")] = "one") -> None:
    typer.echo(thing)


@pytest.mark.parametrize(
    ("command", "parameter", "reason"),
    [
        (argument_with_a_default, "THING", "default"),
        (several_defaults, "--thing", "several values"),
        (default_not_shown, "--thing", "show_default"),
        (argument_with_a_variable, "THING", "environment variable"),
        (empty_default, "--thing", "empty default"),
        (marker_in_help, "--thing", "control character"),
        (control_character_in_help, "--thing", "control character"),
        (html_in_help, "--thing", "HTML"),
    ],
)
def test_what_the_page_cannot_show_fails_naming_the_command_and_the_parameter(
    command: Callable[..., None], parameter: str, reason: str
):
    with pytest.raises(cli_reference.UnsupportedError) as raised:
        probe(command)

    message = str(raised.value)
    assert f"`{parameter}` of `tool probe`" in message
    assert reason in message


@pytest.mark.parametrize(
    ("text", "reason"), [("Probe.\n\n\b\nKept as it is.", "control character"), ("Probe <it>.", "HTML")]
)
@pytest.mark.parametrize("part", ["help", "epilog"])
def test_a_command_whose_text_the_page_cannot_show_fails_naming_it(part: str, text: str, reason: str):
    application = typer.Typer(name="tool", help="A tool.")
    application.callback()(lambda: None)
    said = {"help": "Probe.", "epilog": None, part: text}
    application.command("probe", help=said["help"], epilog=said["epilog"])(lambda: None)

    with pytest.raises(cli_reference.UnsupportedError) as raised:
        cli_reference.render(application)

    assert f"The {part} of `tool probe`" in str(raised.value)
    assert reason in str(raised.value)


def test_angle_brackets_in_backticks_are_kept():
    def command(*, thing: Annotated[str | None, typer.Option(help="A thing, as `<name>`.")] = None) -> None:
        typer.echo(thing)

    assert "A thing, as `<name>`." in probe(command)


def test_what_the_page_cannot_show_fails_the_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    page = tmp_path / "cli-reference.md"
    monkeypatch.setattr(cli_reference, "PAGE", page)
    application = typer.Typer(name="tool", help="A tool.")
    application.callback()(lambda: None)
    application.command("probe", help="Probe.")(empty_default)
    monkeypatch.setattr(cli_reference, "app", application)

    assert cli_reference.main([]) == 2
    assert "`--thing` of `tool probe`" in capsys.readouterr().err
    assert not page.exists()


def test_every_command_of_osh_has_a_section():
    page = cli_reference.render()
    found = list(paths(typer.main.get_command(app), "osh"))

    assert {"osh", "osh logs", "osh builds rebuild"} <= set(found)
    for path in found:
        level = "##" if len(path.split()) < 3 else "###"
        assert f"\n{level} `{path}`\n" in page
