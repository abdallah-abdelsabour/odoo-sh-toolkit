import pytest
import typer
from typer.testing import CliRunner

from odouche_cli import _context
from odouche_cli.app import app


runner = CliRunner()

COMMANDS = ["auth", "branches", "builds", "logs", "projects", "ssh"]


# What each shell's completion script sets to ask `osh` for the words that complete a line.
def bash(line: str) -> dict[str, str]:
    return {"_OSH_COMPLETE": "complete_bash", "COMP_WORDS": line, "COMP_CWORD": str(len(line.split(" ")) - 1)}


def zsh(line: str) -> dict[str, str]:
    return {"_OSH_COMPLETE": "complete_zsh", "_TYPER_COMPLETE_ARGS": line}


def fish(line: str) -> dict[str, str]:
    return {"_OSH_COMPLETE": "complete_fish", "_TYPER_COMPLETE_ARGS": line, "_TYPER_COMPLETE_FISH_ACTION": "get-args"}


@pytest.fixture(autouse=True)
def no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a completion that reads the checkout. The `unreached` guard fails one that opens a client."""

    def refuse(*_: str) -> str | None:
        msg = "A completion ran git."
        raise AssertionError(msg)

    monkeypatch.setattr(_context, "_git", refuse)


def test_completion_is_installed_from_the_root():
    options = {name for parameter in typer.main.get_command(app).params for name in parameter.opts}

    assert {"--install-completion", "--show-completion"} <= options


@pytest.mark.parametrize("shell", [bash, zsh, fish])
def test_a_command_name_completes_without_a_client(shell):
    result = runner.invoke(app, [], env=shell("osh "))

    assert result.exit_code == 0
    assert all(command in result.output for command in COMMANDS)


@pytest.mark.parametrize("shell", [bash, zsh, fish])
def test_a_command_name_completes_from_its_first_letters(shell):
    result = runner.invoke(app, [], env=shell("osh b"))

    assert result.exit_code == 0
    assert "branches" in result.output
    assert "builds" in result.output
    assert "logs" not in result.output


@pytest.mark.parametrize("shell", [bash, zsh, fish])
def test_a_command_of_a_group_completes(shell):
    result = runner.invoke(app, [], env=shell("osh builds "))

    assert result.exit_code == 0
    assert all(command in result.output for command in ("list", "show", "watch", "rebuild"))


def test_an_option_name_completes():
    result = runner.invoke(app, [], env=bash("osh logs --f"))

    assert result.output.split() == ["--follow"]


def test_the_values_of_a_choice_complete():
    result = runner.invoke(app, [], env=bash("osh branches list --stage "))

    assert result.output.split() == ["production", "staging", "development", "unknown"]


@pytest.mark.parametrize(
    "line",
    ["osh builds show --project ", "osh builds show --branch ", "osh logs --kind ", "osh logs --build "],
)
def test_a_value_only_odoo_sh_or_the_checkout_knows_is_not_completed(line: str):
    # It would take a request to Odoo.sh, or a run of git, for every press of the tab key.
    result = runner.invoke(app, [], env=bash(line))

    assert result.exit_code == 0
    assert result.output.split() == []
