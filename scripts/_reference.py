"""What the scripts that write a reference page share: the page's tables, and writing or checking it."""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


class UnsupportedError(Exception):
    """Something the page has no rendering for."""


def main(
    argv: Sequence[str] | None, render: Callable[[], str], page: Path, *, description: str, source: str, regenerate: str
) -> int:
    """Write the page, or with `--check` only tell whether it is the one in the tree."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--check", action="store_true", help="Write nothing, and fail when the page is stale.")
    options = parser.parse_args(argv)
    try:
        text = render()
    except UnsupportedError as error:
        sys.stderr.write(f"{error}\n")
        return 2
    if not options.check:
        page.write_text(text, encoding="utf-8")
        return 0
    if page.is_file() and page.read_text(encoding="utf-8") == text:
        return 0
    sys.stderr.write(f"docs/{page.name} is not what {source} give. Run `{regenerate}`.\n")
    return 1


def line(text: str) -> str:
    """Make text one line that a table cell can hold."""
    return " ".join(text.split()).replace("|", "\\|")


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Return a Markdown table."""
    lines = [headers, ["---"] * len(headers), *rows]
    # An empty cell is one space wide, as markdownlint leaves it.
    return "\n".join("|" + "|".join(f" {cell} " if cell else " " for cell in cells) + "|" for cells in lines)
