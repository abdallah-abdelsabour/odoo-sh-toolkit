import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner, Result

import odouche
from odouche_cli import _context  # pyright: ignore[reportPrivateUsage]
from odouche_cli._context import (
    BRANCH_ENV,
    PROJECT_ENV,
    BranchOption,
    ProjectOption,
    checkout_is_of,
    resolve_branch,
    resolve_project,
)
from odouche_cli._errors import DebugOption, OshGroup


ACME = odouche.Project(id=1, name="acme", repository="Acme/Odoo", url="https://www.odoo.sh/project/acme")
ACME_TEST = odouche.Project(id=2, name="acme-test", repository="Acme/Odoo", url="https://www.odoo.sh/project/acme-test")
GLOBEX = odouche.Project(id=3, name="globex", repository="globex/erp", url="https://www.odoo.sh/project/globex")

SSH = "git@github.com:acme/odoo.git"
TOKEN = "ghp_sentinel"

runner = CliRunner()


def run(*args: str, projects: Sequence[odouche.Project] = (ACME, GLOBEX)) -> tuple[Result, list[str]]:
    """Run a stub command that prints what it resolved, and return the lookups it made too."""
    stub = typer.Typer(cls=OshGroup)
    lookups: list[str] = []

    def listed() -> list[odouche.Project]:
        lookups.append("projects")
        return list(projects)

    @stub.callback()
    def root(*, debug: DebugOption = False) -> None:  # pyright: ignore[reportUnusedFunction]
        pass

    @stub.command("project")
    def project_(ctx: typer.Context, project: ProjectOption = None) -> None:  # pyright: ignore[reportUnusedFunction]
        typer.echo(resolve_project(ctx, project, listed))

    @stub.command("branch")
    def branch_(ctx: typer.Context, branch: BranchOption = None) -> None:  # pyright: ignore[reportUnusedFunction]
        typer.echo(resolve_branch(ctx, branch))

    return runner.invoke(stub, list(args)), lookups


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/odoo.git",
        "git@github.com:acme/odoo",
        "ssh://git@github.com/acme/odoo.git",
        "ssh://git@github.com:22/acme/odoo.git",
        "https://github.com/acme/odoo.git",
        "https://github.com/acme/odoo",
        "https://github.com/acme/odoo/",
        f"https://{TOKEN}@github.com/acme/odoo.git",
        f"https://user:{TOKEN}@github.com/acme/odoo.git",
    ],
)
def test_a_github_remote_is_its_owner_and_name(url: str):
    assert _context._github_repository(url) == "acme/odoo"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "url",
    [
        "git@gitlab.com:acme/odoo.git",
        "https://example.com/acme/odoo.git",
        "https://github.com.example.com/acme/odoo.git",
        "https://github.com/acme",
        "https://github.com/acme/odoo/tree/main",
        "/srv/git/odoo.git",
    ],
)
def test_any_other_remote_is_nothing(url: str):
    assert _context._github_repository(url) is None  # pyright: ignore[reportPrivateUsage]


def test_a_checkout_gives_the_project_and_the_branch(checkout: Callable[..., None]):
    checkout("feature-x", origin=SSH)

    project, lookups = run("project")
    branch, _ = run("branch")

    assert (project.exit_code, project.stdout) == (0, "acme\n")
    assert lookups == ["projects"]
    assert (branch.exit_code, branch.stdout) == (0, "feature-x\n")


def test_the_remote_is_the_upstream_of_the_branch_before_origin(
    checkout: Callable[..., None], git: Callable[..., None]
):
    checkout("main", origin="https://github.com/globex/erp.git", fork=SSH)
    git("config", "branch.main.remote", "fork")

    result, _ = run("project")

    assert result.stdout == "acme\n"


def test_a_branch_tracking_a_local_one_falls_back_to_origin(checkout: Callable[..., None], git: Callable[..., None]):
    checkout("main", origin=SSH)
    git("config", "branch.main.remote", ".")

    result, _ = run("project")

    assert result.stdout == "acme\n"


def test_the_checkout_is_of_the_project_of_its_repository_and_no_other(checkout: Callable[..., None]):
    checkout("main", origin=SSH)

    assert checkout_is_of("acme", lambda: [ACME, ACME_TEST, GLOBEX])
    assert checkout_is_of("acme-test", lambda: [ACME, ACME_TEST, GLOBEX])
    assert not checkout_is_of("globex", lambda: [ACME, GLOBEX])
    assert not checkout_is_of("unknown", lambda: [ACME, GLOBEX])


def test_a_checkout_with_no_github_remote_builds_no_project_and_lists_none(checkout: Callable[..., None]):
    checkout("main", origin="git@gitlab.com:acme/odoo.git")
    lookups: list[str] = []

    assert not checkout_is_of("acme", lambda: lookups.append("projects") or [ACME])
    assert lookups == []


def test_the_flag_wins_over_the_variable_and_the_checkout(
    monkeypatch: pytest.MonkeyPatch, checkout: Callable[..., None]
):
    checkout("feature-x", origin=SSH)
    monkeypatch.setenv(PROJECT_ENV, "from-env")
    monkeypatch.setenv(BRANCH_ENV, "env-branch")

    project, lookups = run("project", "--project", "from-flag")
    branch, _ = run("branch", "--branch", "flag-branch")

    assert project.stdout == "from-flag\n"
    assert lookups == []
    assert branch.stdout == "flag-branch\n"


def test_the_variable_wins_over_the_checkout(monkeypatch: pytest.MonkeyPatch, checkout: Callable[..., None]):
    checkout("feature-x", origin=SSH)
    monkeypatch.setenv(PROJECT_ENV, "from-env")
    monkeypatch.setenv(BRANCH_ENV, "env-branch")

    project, lookups = run("project")
    branch, _ = run("branch")

    assert project.stdout == "from-env\n"
    assert lookups == []
    assert branch.stdout == "env-branch\n"


def test_an_empty_variable_is_not_a_value(monkeypatch: pytest.MonkeyPatch, checkout: Callable[..., None]):
    checkout("feature-x", origin=SSH)
    monkeypatch.setenv(PROJECT_ENV, "")
    monkeypatch.setenv(BRANCH_ENV, "")

    project, _ = run("project")
    branch, _ = run("branch")

    assert project.stdout == "acme\n"
    assert branch.stdout == "feature-x\n"


def test_an_empty_flag_is_a_usage_error_and_not_the_checkout(checkout: Callable[..., None]):
    checkout("feature-x", origin=SSH)

    project, lookups = run("project", "--project", "")
    branch, _ = run("branch", "--branch", "")

    assert (project.exit_code, project.stdout) == (2, "")
    assert lookups == []
    assert (branch.exit_code, branch.stdout) == (2, "")


def test_a_tag_named_as_the_branch_does_not_change_its_name(checkout: Callable[..., None], git: Callable[..., None]):
    checkout("feature-x", origin=SSH)
    git("tag", "feature-x")

    result, _ = run("branch")

    assert result.stdout == "feature-x\n"


def test_a_repository_of_two_projects_is_an_error_that_lists_them(checkout: Callable[..., None]):
    checkout("feature-x", origin=SSH)

    result, lookups = run("project", projects=(ACME, ACME_TEST, GLOBEX))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "acme, acme-test" in click.unstyle(result.stderr)
    assert "--project" in click.unstyle(result.stderr)
    assert lookups == ["projects"]


def test_a_repository_of_no_project_exits_4(checkout: Callable[..., None]):
    checkout("feature-x", origin="git@github.com:other/thing.git")

    result, _ = run("project")

    assert result.exit_code == 4
    assert result.stdout == ""
    assert "other/thing" in result.stderr


def test_a_detached_head_is_no_branch_and_still_a_project(checkout: Callable[..., None], git: Callable[..., None]):
    checkout("feature-x", origin=SSH)
    git("checkout", "--quiet", "--detach")

    branch, _ = run("branch")
    project, _ = run("project")

    assert branch.exit_code == 2
    assert branch.stdout == ""
    assert project.stdout == "acme\n"


def test_outside_a_repository_exits_2_and_names_the_three_sources():
    project, lookups = run("project")
    branch, _ = run("branch")

    assert project.exit_code == 2
    assert project.stdout == ""
    assert lookups == []
    for source in ("--project", PROJECT_ENV, "checkout"):
        assert source in click.unstyle(project.stderr)
    assert branch.exit_code == 2
    for source in ("--branch", BRANCH_ENV, "checkout"):
        assert source in click.unstyle(branch.stderr)


def test_a_remote_that_is_not_on_github_is_no_project(checkout: Callable[..., None]):
    checkout("feature-x", origin="git@gitlab.com:acme/odoo.git")

    result, lookups = run("project")

    assert result.exit_code == 2
    assert lookups == []


def test_without_git_installed_there_is_no_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, checkout: Callable[..., None]
):
    checkout("feature-x", origin=SSH)
    monkeypatch.setenv("PATH", str(tmp_path))

    project, _ = run("project")
    branch, _ = run("branch")

    assert project.exit_code == 2
    assert branch.exit_code == 2


def test_a_git_that_does_not_answer_is_no_value(monkeypatch: pytest.MonkeyPatch):
    def hang(*_: object, **__: object) -> None:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(subprocess, "run", hang)

    result, _ = run("branch")

    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("args", "env", "said"),
    [
        (("project", "--project", "acme"), {}, "Project acme, from --project."),
        (("project",), {PROJECT_ENV: "acme"}, f"Project acme, from {PROJECT_ENV}."),
        (("project",), {}, "Project acme, from the git checkout (acme/odoo)."),
        (("branch", "--branch", "main"), {}, "Branch main, from --branch."),
        (("branch",), {BRANCH_ENV: "main"}, f"Branch main, from {BRANCH_ENV}."),
        (("branch",), {}, "Branch feature-x, from the git checkout."),
    ],
)
def test_debug_names_the_source_and_never_the_address(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    env: dict[str, str],
    said: str,
    checkout: Callable[..., None],
):
    checkout("feature-x", origin=f"https://{TOKEN}@github.com/acme/odoo.git")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    result, _ = run("--debug", *args)
    quiet, _ = run(*args)

    assert result.exit_code == 0
    assert result.stderr == f"{said}\n"
    assert TOKEN not in result.output
    assert quiet.stderr == ""
