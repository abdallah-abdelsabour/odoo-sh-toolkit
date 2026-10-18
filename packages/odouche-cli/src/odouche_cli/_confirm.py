"""Asking before a change on Odoo.sh: the question on stderr, the answer from stdin, nothing on stdout."""

import sys
from typing import TextIO

import typer


def confirmed(question: str) -> bool:
    """Ask on stderr and read the answer from stdin: only `y` or `yes` is a yes. Nothing goes to stdout."""
    typer.echo(f"{question} [y/N]: ", nl=False, err=True)
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def stdin_is_terminal() -> bool:
    return is_terminal(sys.stdin)


def stderr_is_terminal() -> bool:
    return is_terminal(sys.stderr)


def is_terminal(stream: TextIO | None) -> bool:
    """Tell whether a standard stream is a terminal, which a closed one is not."""
    return stream is not None and stream.isatty()
