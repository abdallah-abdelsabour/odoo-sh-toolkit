"""The two tables of `docs/cli.md` that scripts rely on, held to the code."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer

import odouche
from odouche_cli import _errors
from odouche_cli.app import app


ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "cli.md"
SOURCE = ROOT / "packages" / "odouche-cli" / "src" / "odouche_cli"

# What click and typer exit with themselves: success, a usage error, Ctrl+C.
BUILT_IN = {0, 2, 130}
# What the environment is asked for in the source, as it is written there.
READ = {"odouche.SESSION_ENV": odouche.SESSION_ENV, '"NO_COLOR"': "NO_COLOR"}
_ENVIRON = re.compile(r"os\.(?:environ\.get\(|environ\[|getenv\()([^,)\]]+)|(\S+) (?:not )?in os\.environ")
_ENVIRON_WORD = re.compile(r"\b(?:environ|getenv)\b")
# What the library's HTTP client reads: no source of `osh` names them.
HTTP_CLIENT = {"HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}
_LITERAL_EXIT = re.compile(r"(?:Exit|\bexit)\(\s*(?:code\s*=\s*)?-?\d")


def first_column(heading: str) -> list[str]:
    """Return the first cell of each row of the first table under a heading of the guide."""
    section = GUIDE.read_text(encoding="utf-8").split(f"\n{heading}\n", 1)[1]
    rows = [line for line in section.split("\n#", 1)[0].splitlines() if line.startswith("|")]
    assert len(rows) > 2, f"No table under {heading}."
    return [row.split("|")[1].strip() for row in rows[2:]]


def read(source: str) -> list[str]:
    """Return each name the environment is asked for in a source, and fail on a read this does not follow."""
    found = [by_call or by_test for by_call, by_test in _ENVIRON.findall(source)]
    assert len(found) == len(_ENVIRON_WORD.findall(source)), "The environment is read in a way this does not follow."
    return found


def declared(command: Any) -> Iterator[str]:
    """Walk a command's tree and yield each environment variable an option reads."""
    for parameter in command.params:
        variables = parameter.envvar or ()
        yield from [variables] if isinstance(variables, str) else variables
    children: dict[str, Any] = getattr(command, "commands", {})
    for child in children.values():
        yield from declared(child)


def test_the_exit_code_table_has_every_code_and_no_other():
    constants = {value for name, value in vars(_errors).items() if name.startswith("EXIT_") and isinstance(value, int)}
    mapped = {code for _, code, _ in _errors.EXIT_CODES}
    cells = first_column("### Exit codes")

    listed = [int(cell) for cell in cells if cell.isdecimal()]

    assert sorted(listed) == sorted(BUILT_IN | constants | mapped)
    assert [cell for cell in cells if not cell.isdecimal()] == ["24 to 29"]


def test_no_exit_code_is_written_out_of_the_table():
    literal = [
        path.name for path in sorted(SOURCE.glob("*.py")) if _LITERAL_EXIT.search(path.read_text(encoding="utf-8"))
    ]

    # A code is an `EXIT_*` constant of `_errors`, which the table is held to.
    assert literal == []


@pytest.mark.parametrize("line", ["raise typer.Exit(42)", "ctx.exit(3)", "sys.exit(1)", "raise typer.Exit(code=4)"])
def test_an_exit_code_written_out_is_found(line: str):
    assert _LITERAL_EXIT.search(line)


@pytest.mark.parametrize("line", ["raise typer.Exit(EXIT_DECLINED)", "raise typer.Exit(code)", "raise typer.Exit"])
def test_a_named_exit_code_is_not(line: str):
    assert not _LITERAL_EXIT.search(line)


def test_the_variable_table_has_every_variable_an_option_reads():
    cells = first_column("### Environment variables")
    listed = {name for cell in cells for name in re.findall(r"`([A-Z_]+)`", cell)}

    options = set(declared(typer.main.get_command(app)))

    assert {"OSH_PROJECT", "OSH_BRANCH", "OSH_DEBUG"} <= options
    assert options <= listed


def test_the_variable_table_has_every_variable_the_source_reads():
    cells = first_column("### Environment variables")
    listed = {name for cell in cells for name in re.findall(r"`([A-Z_]+)`", cell)}

    found = {name for path in sorted(SOURCE.glob("*.py")) for name in read(path.read_text(encoding="utf-8"))}

    # A new read needs a row in `READ`, and so in the table.
    assert found == set(READ)
    assert set(READ.values()) <= listed


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ('os.environ.get("OSH_PLAIN")', '"OSH_PLAIN"'),
        ('os.environ["OSH_PLAIN"]', '"OSH_PLAIN"'),
        ('os.getenv("OSH_PLAIN", "")', '"OSH_PLAIN"'),
        ('if "OSH_PLAIN" in os.environ:', '"OSH_PLAIN"'),
        ('if "OSH_PLAIN" not in os.environ:', '"OSH_PLAIN"'),
    ],
)
def test_every_way_of_reading_a_variable_is_followed(source: str, name: str):
    assert read(source) == [name]


@pytest.mark.parametrize("source", ["dict(os.environ)", "from os import getenv", "os.environ.pop(name)"])
def test_a_read_that_is_not_followed_fails(source: str):
    with pytest.raises(AssertionError, match="does not follow"):
        read(source)


def test_the_variable_table_has_the_variables_the_http_client_reads():
    cells = first_column("### Environment variables")
    listed = {name for cell in cells for name in re.findall(r"`([A-Z_]+)`", cell)}

    assert listed >= HTTP_CLIENT
