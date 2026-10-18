import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import odouche
from odouche_cli import _output
from odouche_cli._context import BRANCH_ENV, PROJECT_ENV


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run in an empty directory, with none of `osh`'s variables set and no git configuration of the user's."""
    monkeypatch.delenv(PROJECT_ENV, raising=False)
    monkeypatch.delenv(BRANCH_ENV, raising=False)
    monkeypatch.delenv("OSH_DEBUG", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    # A stray repository above `tmp_path` is not this test's checkout.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def unreached(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., object]]:
    """Fail a test that reaches the library's client, login or logout, and return what stands for the client.

    A file's own stand-ins replace these.
    """
    reached: list[str] = []

    def refuse(name: str) -> Callable[..., object]:
        def refused(*_: object, **__: object) -> object:
            reached.append(name)
            msg = f"odouche.{name} has no stand-in."
            raise AssertionError(msg)

        return refused

    refusals = {name: refuse(name) for name in ("Client", "login", "logout")}
    for name, refused in refusals.items():
        monkeypatch.setattr(odouche, name, refused)
    yield refusals["Client"]
    # The command reports the error as its own, with an exit code a test may expect.
    assert reached == [], "A command reached the library with no stand-in."


@pytest.fixture(autouse=True)
def read_only_clients(unreached: Callable[..., object]) -> Iterator[None]:
    """Fail a test whose command built a client that writes: only `osh builds rebuild` and `osh firewall allow` may."""
    yield
    if odouche.Client is unreached:
        return
    # `unreached` is asked for so that the test's stand-in is still in place here.
    modes: list[bool] | None = getattr(odouche.Client, "modes", None)
    assert modes is not None, "The stand-in for `odouche.Client` has to record each `read_only` in `modes`."
    assert all(modes), "A command built a client that is not read-only."


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make stdout a terminal that takes colour, for `osh` and for Rich."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("TTY_COMPATIBLE", "1")
    monkeypatch.setattr(_output, "_is_terminal", lambda: True)


def _git(*args: str) -> None:
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args], check=True)  # noqa: S603, S607


@pytest.fixture
def git() -> Callable[..., None]:
    """Run a git command in the current directory."""
    return _git


@pytest.fixture
def checkout() -> Callable[..., None]:
    """Make the current directory a repository on `branch`, with one commit and these remotes."""

    def checkout(branch: str = "feature-x", **remotes: str) -> None:
        _git("init", "--quiet", "--initial-branch", branch)
        _git("commit", "--quiet", "--allow-empty", "--message", "first")
        for name, url in remotes.items():
            _git("remote", "add", name, url)

    return checkout
