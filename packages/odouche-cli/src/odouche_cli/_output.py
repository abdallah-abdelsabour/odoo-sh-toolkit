"""A command's result as a table or as JSON, rendered once for every `osh` command.

Only the result goes to stdout. The JSON keys are the library models' attribute names, so the
library's reference documents the shape.
"""

import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Annotated, TextIO

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text


# Wide enough that a piped table is never wrapped or truncated.
_PLAIN_WIDTH = 100_000

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_SPACE = re.compile(r"[\t\n\v\f\r\u2028\u2029]+")
_LINE_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
_CSI = r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
_STRING = r"[^\x07\x1b\x9c]*"
_END = r"(?:\x07|\x1b\\|\x9c)"
# A 7-bit string that was cut goes to the end of the line. An 8-bit introducer that nothing ends
# is a character of text decoded twice: only it goes, and `bare` is the text after it.
_SEQUENCE = re.compile(
    rf"{_CSI}"
    rf"|\x1b[\]PX^_]{_STRING}{_END}?"  # OSC, DCS, SOS, PM, APC
    rf"|[\x90\x98\x9d\x9e\x9f](?:{_STRING}{_END}|(?P<bare>{_STRING}))"  # the same, in 8 bits
    r"|\x1b[ -/]*[0-~]"  # any other escape
)
_BARE_SEQUENCE = re.compile(_CSI)
_JSON_UNESCAPED = re.compile(r"[\x7f-\x9f]")


class Format(StrEnum):
    """How a result is printed."""

    TABLE = "table"
    JSON = "json"


FormatOption = Annotated[
    Format,
    typer.Option("--format", help="How to print the result."),
]


@dataclass(frozen=True, slots=True)
class Column[T]:
    """One column of a table: its header, how to read the cell from a row, and how to style it."""

    header: str
    value: Callable[[T], object]
    style: Callable[[T], str | None] | None = None
    """The cell's Rich style, which shows on a terminal only. The cell's text has to say it too."""


def strip_control(text: str) -> str:
    """Remove the control characters, which are C0 with ESC, DEL and C1: they can drive a terminal.

    A run of those that are whitespace, and of the Unicode line and paragraph separators, becomes one space.
    """
    return _CONTROL.sub("", _CONTROL_SPACE.sub(" ", text))


def strip_line_control(text: str) -> str:
    """Remove the escape sequences from a line of text that is not ours, then the control characters but the tab."""
    return _LINE_CONTROL.sub("", _SEQUENCE.sub(_unsequenced, text))


def _unsequenced(found: re.Match[str]) -> str:
    """Return what a sequence leaves: nothing, or the text after a bare 8-bit introducer."""
    bare = found["bare"]
    # No string starts in it: each would end where this one does not.
    return _BARE_SEQUENCE.sub("", bare) if bare else ""


def to_json(value: object) -> object:
    """Turn a model into what `json` can dump, refusing any type it does not know."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return to_json(value.value)
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            msg = "A datetime without an offset cannot be printed as JSON."
            raise ValueError(msg)
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_json(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json(getattr(value, field.name)) for field in fields(value)}
    # The type only: the value could be a secret.
    msg = f"{type(value).__name__} cannot be printed as JSON."
    raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class Output:
    """Prints results in the format the user chose."""

    format: Format = Format.TABLE

    def rows[T](self, items: Iterable[T], columns: Sequence[Column[T]], *, empty: str) -> None:
        """Print a list of models. With none, a table is the `empty` line on stderr."""
        items = list(items)
        if self.format is Format.JSON:
            _echo_json(items, indent=2)
            return
        if not items:
            typer.echo(empty, err=True)
            return
        plain = _plain()
        table = Table(box=None if plain else box.SIMPLE_HEAD, pad_edge=not plain)
        for column in columns:
            table.add_column(Text(column.header))
        for item in items:
            table.add_row(*(_cell(column, item) for column in columns))
        _print(table, plain=plain)

    def one[T](self, item: T, columns: Sequence[Column[T]]) -> None:
        """Print one model, as a table of its columns' headers and values."""
        if self.format is Format.JSON:
            _echo_json(item, indent=2)
            return
        table = Table(box=None, show_header=False, pad_edge=False)
        table.add_column(style="bold")
        table.add_column()
        for column in columns:
            table.add_row(Text(column.header), _cell(column, item))
        _print(table, plain=_plain())

    def stream[T](self, items: Iterable[T], line: Callable[[T], str]) -> None:
        """Print models as they arrive: one JSON object per line, or `line` of each."""
        for item in items:
            if self.format is Format.JSON:
                _echo_json(item, indent=None)
            else:
                typer.echo(strip_control(line(item)))

    def lines[T](self, items: Iterable[T], text: Callable[[T], str], *, strip: bool | None = None) -> None:
        """Print lines of text that is not ours as they arrive: as a stream of JSON, or `text` of each.

        A terminal gets them without their escape sequences and control characters, tabs kept, and
        anything else gets them as they are. `strip` forces one or the other. Each line is flushed.
        """
        if self.format is Format.JSON:
            self.stream(items, text)
            return
        if strip is None:
            strip = _is_terminal()
        for item in items:
            _echo_line(strip_line_control(text(item)) if strip else text(item))


def _echo_json(value: object, *, indent: int | None) -> None:
    dumped = json.dumps(to_json(value), indent=indent, ensure_ascii=False)
    try:
        dumped.encode(sys.stdout.encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        dumped = json.dumps(to_json(value), indent=indent)
    # `json` escapes C0 and leaves DEL and C1 as they are.
    typer.echo(_JSON_UNESCAPED.sub(lambda found: f"\\u{ord(found[0]):04x}", dumped))


def _echo_line(text: str) -> None:
    stream = _stdout()
    # A character the stream cannot encode is escaped: the error would quote it.
    encoding = stream.encoding or "utf-8"
    # `color` keeps click from removing the escape sequences when stdout is not a terminal.
    typer.echo(text.encode(encoding, "backslashreplace").decode(encoding), file=stream, color=True)


def _cell[T](column: Column[T], item: T) -> Text:
    """Make a cell of literal text, so a value holding `[bold]` or an escape sequence is not obeyed."""
    value = column.value(item)
    style = column.style(item) if column.style else None
    return Text("" if value is None else strip_control(str(value)), style=style or "")


# What the tests replace: where the lines go.
def _stdout() -> TextIO:
    return sys.stdout


def _is_terminal() -> bool:
    return sys.stdout.isatty()


def _plain() -> bool:
    """Whether to print without colour or box drawing."""
    return bool(os.environ.get("NO_COLOR")) or not _is_terminal()


def _print(table: Table, *, plain: bool) -> None:
    if not plain:
        Console(file=sys.stdout, highlight=False).print(table)
        return
    # Not a terminal even where colour is forced, as it is on a CI runner.
    console = Console(file=sys.stdout, force_terminal=False, no_color=True, width=_PLAIN_WIDTH, highlight=False)
    with console.capture() as capture:
        console.print(table)
    # Rich pads every cell to its column's width.
    for line in capture.get().removesuffix("\n").split("\n"):
        typer.echo(line.rstrip())
