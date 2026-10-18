import io
import json
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

import pytest
import typer
from typer.testing import CliRunner

import odouche
from odouche_cli import _output
from odouche_cli._output import Column, Format, Output, strip_control, strip_line_control, to_json
from odouche_cli.app import app, root


SENTINEL = "sentinel-session-value"
ANSI = re.compile(r"\x1b\[")
BOX = re.compile(r"[─-╿]")


class Colour(StrEnum):
    RED = "red"


@dataclass(frozen=True, slots=True)
class Owner:
    name: str


@dataclass(frozen=True, slots=True)
class Thing:
    id: int
    colour: Colour
    made_at: datetime
    owner: Owner
    note: str | None = None


THING = Thing(1, Colour.RED, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2))), Owner("ann"))
OTHER = Thing(2, Colour.RED, datetime(2026, 1, 3, tzinfo=UTC), Owner("bob"), note="x" * 200)
THING_JSON = {
    "id": 1,
    "colour": "red",
    "made_at": "2026-01-02T03:04:05+02:00",
    "owner": {"name": "ann"},
    "note": None,
}

COLUMNS = [
    Column[Thing]("ID", lambda thing: thing.id),
    Column[Thing]("Owner", lambda thing: thing.owner.name),
    Column[Thing]("Note", lambda thing: thing.note),
]

runner = CliRunner()


def test_json_rows_are_the_models_and_nothing_else(capsys: pytest.CaptureFixture[str]):
    Output(Format.JSON).rows([THING], COLUMNS, empty="No things.")

    captured = capsys.readouterr()
    assert json.loads(captured.out) == [THING_JSON]
    assert captured.err == ""


def test_one_model_has_the_same_json_shape_as_a_row(capsys: pytest.CaptureFixture[str]):
    Output(Format.JSON).one(THING, COLUMNS)

    assert json.loads(capsys.readouterr().out) == THING_JSON


def test_a_piped_table_is_plain_and_unwrapped(capsys: pytest.CaptureFixture[str]):
    Output().rows([THING, OTHER], COLUMNS, empty="No things.")

    lines = capsys.readouterr().out.splitlines()
    assert [line.split() for line in lines] == [["ID", "Owner", "Note"], ["1", "ann"], ["2", "bob", "x" * 200]]
    assert all(line == line.rstrip() for line in lines)


@pytest.mark.parametrize("variable", ["GITHUB_ACTIONS", "FORCE_COLOR"])
def test_a_piped_table_stays_plain_where_colour_is_forced(
    variable: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv(variable, "true")
    output = Output()

    output.rows([THING], COLUMNS, empty="No things.")
    output.one(THING, COLUMNS)

    out = capsys.readouterr().out
    assert not ANSI.search(out)
    assert not BOX.search(out)


@pytest.mark.usefixtures("terminal")
def test_a_terminal_table_has_a_rule_under_its_header(capsys: pytest.CaptureFixture[str]):
    Output().rows([THING], COLUMNS, empty="No things.")

    assert BOX.search(capsys.readouterr().out)


@pytest.mark.usefixtures("terminal")
def test_no_color_makes_a_terminal_table_plain(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("NO_COLOR", "1")

    Output().rows([THING], COLUMNS, empty="No things.")

    out = capsys.readouterr().out
    assert not ANSI.search(out)
    assert not BOX.search(out)


def test_one_model_as_a_table_pairs_headers_with_values(capsys: pytest.CaptureFixture[str]):
    Output().one(THING, COLUMNS)

    lines = capsys.readouterr().out.splitlines()
    assert [line.split() for line in lines] == [["ID", "1"], ["Owner", "ann"], ["Note"]]


def test_a_cell_is_not_read_as_markup(capsys: pytest.CaptureFixture[str]):
    Output().rows([Owner("[bold]ann[/bold] :smile:")], [Column[Owner]("Name", lambda owner: owner.name)], empty="")

    assert "[bold]ann[/bold] :smile:" in capsys.readouterr().out


def test_an_empty_result_is_an_empty_json_list(capsys: pytest.CaptureFixture[str]):
    Output(Format.JSON).rows([], COLUMNS, empty="No things.")

    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert captured.err == ""


def test_an_empty_table_is_one_line_on_stderr(capsys: pytest.CaptureFixture[str]):
    Output().rows([], COLUMNS, empty="No things.")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "No things.\n"


def test_a_json_stream_prints_each_row_as_it_arrives(capsys: pytest.CaptureFixture[str]):
    seen: list[str] = []

    def things() -> Iterator[Thing]:
        yield THING
        seen.append(capsys.readouterr().out)
        yield OTHER

    Output(Format.JSON).stream(things(), lambda thing: thing.owner.name)

    assert [json.loads(line) for line in seen[0].splitlines()] == [THING_JSON]
    assert json.loads(capsys.readouterr().out)["id"] == OTHER.id


def test_a_table_stream_prints_the_line_of_each_row(capsys: pytest.CaptureFixture[str]):
    Output().stream(iter([THING, OTHER]), lambda thing: thing.owner.name)

    assert capsys.readouterr().out == "ann\nbob\n"


def test_a_datetime_without_an_offset_is_refused():
    with pytest.raises(ValueError, match="offset"):
        to_json(datetime(2026, 1, 2))  # noqa: DTZ001 - the naive datetime is the case


def test_an_unknown_type_is_refused_without_its_value():
    with pytest.raises(TypeError, match="Secret") as raised:
        to_json([odouche.Secret(SENTINEL)])

    assert SENTINEL not in str(raised.value)


def run(*args: str):
    """Run a stub command under the real root callback."""
    stub = typer.Typer()
    stub.callback()(root)

    @stub.command()
    def show(ctx: typer.Context) -> None:  # pyright: ignore[reportUnusedFunction]
        ctx.obj.rows([THING], COLUMNS, empty="No things.")

    return runner.invoke(stub, [*args, "show"])


def test_the_format_option_selects_json():
    result = run("--format", "json")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [THING_JSON]


def test_the_format_defaults_to_a_table():
    assert run().stdout.split()[:3] == ["ID", "Owner", "Note"]


def test_an_unknown_format_is_a_usage_error():
    result = runner.invoke(app, ["--format", "yaml", "--version"])

    assert result.exit_code == 2


CONTROLS = "".join(map(chr, [*range(0x20), *range(0x7F, 0xA0)]))


def test_strip_control_removes_c0_del_and_c1_and_nothing_else():
    assert strip_control(f"a{CONTROLS}b é\xa0~") == "a b é\xa0~"


def test_strip_control_makes_whitespace_and_line_separators_a_space():
    assert strip_control("fix:\tthing\r\nnext\v\fone\u2028two\u2029three") == "fix: thing next one two three"


@pytest.mark.parametrize(
    ("line", "stripped"),
    [
        ("\x1b[31mERROR\x1b[0m coloured", "ERROR coloured"),
        ("title \x1b]0;owned\x07 after", "title  after"),
        ("title \x1b]0;owned\x1b\\ after", "title  after"),
        ("c1 \x9b31mred\x9bm \x9d0;t\x9c end", "c1 red  end"),
        ("charset \x1b(B done", "charset  done"),
        ("\tkept\x00\x08\x0b\x0c\r\x7f\x85", "\tkept"),
        ("cut \x1b]0;owned", "cut "),
        ("cut \x1bP1$r", "cut "),
        ("cut \x1b[31", "cut 31"),
        ("cut \x1b", "cut "),
        ("Stra\xc3\x9fe 12 failed: reason", "Stra\xc3e 12 failed: reason"),
        ("bare \x9d one \x9bm\x90 two \x1b[0m end", "bare  one  two  end"),
        ("bare \x9d then \x9d0;t\x9c end", "bare  end"),
    ],
    ids=[
        "CSI",
        "OSC",
        "OSC ended by ST",
        "C1",
        "other escape",
        "control",
        "cut OSC",
        "cut DCS",
        "cut CSI",
        "cut ESC",
        "text decoded twice",
        "bare C1 introducers",
        "bare C1 introducer before an ended one",
    ],
)
def test_strip_line_control_removes_whole_sequences_and_control_characters_but_the_tab(line: str, stripped: str):
    assert strip_line_control(line) == stripped


@pytest.mark.parametrize(
    "line",
    [
        "\x1b]" * 32768,
        "\x9d" * 65536,
        "\x1b[" * 32768,
        "\x1bP" * 32768,
        "\x9d" * 60000 + "\x1b[",
        "\x9d\x9b" * 32768,
    ],
    ids=["OSC", "C1 OSC", "CSI", "DCS", "C1 OSC before a cut CSI", "C1 OSC and CSI"],
)
def test_a_long_line_of_sequences_that_never_end_is_stripped_quickly(line: str):
    started = time.perf_counter()

    assert strip_line_control(line) == ""
    assert time.perf_counter() - started < 5


def test_json_escapes_what_stdout_cannot_encode(monkeypatch: pytest.MonkeyPatch):
    sent = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(sent, encoding="cp1252"))
    owner = Owner("café → done")

    Output(Format.JSON).rows([owner], [], empty="")
    sys.stdout.flush()

    printed = sent.getvalue().decode("cp1252")
    assert json.loads(printed) == [{"name": owner.name}]
    assert "→" not in printed


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\x1c", "\n", "\r"])
def test_a_line_separator_in_a_cell_does_not_add_a_row(separator: str, capsys: pytest.CaptureFixture[str]):
    output = Output()
    owner = Owner(f"ann{separator}2  bob")
    columns = [Column[Owner]("Name", lambda owner: owner.name)]

    output.rows([owner], columns, empty="")
    rows = capsys.readouterr().out
    output.one(owner, columns)
    one = capsys.readouterr().out

    assert len(rows.splitlines()) == 2
    assert len(one.splitlines()) == 1


def test_a_table_stream_line_has_no_control_character(capsys: pytest.CaptureFixture[str]):
    Output().stream([Owner("\x1b[2Jann\nbob\x9b31m")], lambda owner: f"Logged in as {owner.name}.")

    assert capsys.readouterr().out == "Logged in as [2Jann bob31m.\n"


def test_a_table_cell_has_no_control_character(capsys: pytest.CaptureFixture[str]):
    output = Output()
    owner = Owner(f"\x1b[2Jann{CONTROLS}")
    columns = [Column[Owner]("Name", lambda owner: owner.name)]

    output.rows([owner], columns, empty="")
    output.one(owner, columns)

    assert capsys.readouterr().out == "Name\n[2Jann\nName  [2Jann\n"


def test_json_escapes_every_control_character(capsys: pytest.CaptureFixture[str]):
    owner = Owner(f"ann{CONTROLS}é")

    Output(Format.JSON).one(owner, [])

    out = capsys.readouterr().out
    assert not set(out) & set(CONTROLS.replace("\n", ""))
    assert "é" in out
    assert json.loads(out) == {"name": owner.name}


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize(("variable", "value"), [("TTY_COMPATIBLE", "0"), ("FORCE_COLOR", "")])
def test_a_terminal_table_has_no_colour_where_the_environment_refuses_it(
    variable: str, value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("TTY_COMPATIBLE")
    monkeypatch.setenv(variable, value)

    Output().one(THING, COLUMNS)

    assert not ANSI.search(capsys.readouterr().out)


STYLED = [Column[Owner]("Name", lambda owner: owner.name, lambda owner: "red" if owner.name == "bob" else None)]


@pytest.mark.usefixtures("terminal")
def test_a_styled_cell_is_coloured_on_a_terminal(capsys: pytest.CaptureFixture[str]):
    Output().rows([Owner("ann"), Owner("bob")], STYLED, empty="")

    out = capsys.readouterr().out
    assert re.search(r"\x1b\[31mbob *\x1b\[0m", out)
    assert not re.search(r"\x1b\[31mann", out)


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("no_color", [True, False])
def test_a_styled_cell_is_its_text_alone_without_colour(
    no_color: bool,  # noqa: FBT001
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.setattr(_output, "_is_terminal", lambda: False)

    Output().rows([Owner("bob")], STYLED, empty="")

    assert capsys.readouterr().out == "Name\nbob\n"
