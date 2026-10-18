"""The project and the branch a command works on: the flag, the environment, then the git checkout.

The first source that yields a value wins. Resolving a name asks Odoo.sh nothing but the projects,
through the command's own way to list them, and only for a project read from the checkout.
"""

import re
import subprocess
from collections.abc import Callable, Iterable
from typing import Annotated, override

import typer

import odouche


PROJECT_ENV = "OSH_PROJECT"
BRANCH_ENV = "OSH_BRANCH"

_GIT_TIMEOUT = 5.0

_GITHUB = re.compile(
    r"(?:git@github\.com:|ssh://git@github\.com(?::\d+)?/|https://(?:[^/@\s]+@)?github\.com/)"
    r"(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?",
    re.IGNORECASE,
)

ProjectOption = Annotated[
    str | None,
    typer.Option("--project", envvar=PROJECT_ENV, help="The project's name. Default: the git checkout's."),
]
BranchOption = Annotated[
    str | None,
    typer.Option("--branch", envvar=BRANCH_ENV, help="The branch's name. Default: the git checkout's."),
]


class ContextError(typer.BadParameter):
    """A usage error that is no option's value, such as no source naming the project or the branch."""

    @override
    def format_message(self) -> str:
        return self.message


def resolve_project(ctx: typer.Context, value: str | None, projects: Callable[[], Iterable[odouche.Project]]) -> str:
    """Return the name of the project to work on.

    `value` is what the command's `project` parameter, a `ProjectOption`, received. `projects` is
    called only when the project comes from the checkout, to find which one builds its repository.
    """
    if value is not None:
        _refuse_empty(ctx, value, "--project")
        _debug(ctx, f"Project {value}, from {_source(ctx, 'project', '--project', PROJECT_ENV)}.")
        return value
    repository = _checkout_repository()
    if repository is None:
        msg = (
            f"No project. Give one with --project or {PROJECT_ENV}, or run from a checkout whose "
            "remote is the project's GitHub repository."
        )
        raise ContextError(msg, ctx=ctx)
    names = [project.name for project in projects() if project.repository.casefold() == repository.casefold()]
    if not names:
        msg = f"No project you can reach builds {repository}."
        raise odouche.NotFoundError(msg)
    if len(names) > 1:
        msg = (
            f"{repository} is built by several projects: {', '.join(names)}. Pick one with --project or {PROJECT_ENV}."
        )
        raise ContextError(msg, ctx=ctx)
    _debug(ctx, f"Project {names[0]}, from the git checkout ({repository}).")
    return names[0]


def resolve_branch(ctx: typer.Context, value: str | None) -> str:
    """Return the name of the branch to work on.

    `value` is what the command's `branch` parameter, a `BranchOption`, received.
    """
    if value is not None:
        _refuse_empty(ctx, value, "--branch")
        _debug(ctx, f"Branch {value}, from {_source(ctx, 'branch', '--branch', BRANCH_ENV)}.")
        return value
    branch = checkout_branch()
    if branch is None:
        msg = f"No branch. Give one with --branch or {BRANCH_ENV}, or run from a checkout that is on a branch."
        raise ContextError(msg, ctx=ctx)
    _debug(ctx, f"Branch {branch}, from the git checkout.")
    return branch


def find_branch(client: odouche.Client, project: str, name: str) -> odouche.Branch:
    """Return the project's branch of that name, as Odoo.sh knows it."""
    for branch in client.branches(project):
        if branch.name == name:
            return branch
    msg = f"Project {project} has no branch {name}."
    raise odouche.NotFoundError(msg)


def project_branch(
    ctx: typer.Context, client: odouche.Client, project: str | None, name: str
) -> tuple[str, odouche.Branch]:
    """Return the project's name and its branch of that name."""
    project_name = resolve_project(ctx, project, client.projects)
    return project_name, find_branch(client, project_name, name)


def find_build(client: odouche.Client, project: str, branch: odouche.Branch, build_id: int | None) -> odouche.Build:
    """Return the branch's build of that number, or its latest."""
    if build_id is not None:
        for build in client.builds(branch):
            if build.id == build_id:
                return build
        msg = f"Build {build_id} is not among the latest builds of branch {branch.name} of {project}."
        raise odouche.NotFoundError(msg)
    latest = client.latest_build(branch)
    if latest is None:
        msg = f"Branch {branch.name} of {project} has no build."
        raise odouche.NotFoundError(msg)
    return latest


def stage_of(branch: odouche.Branch) -> str:
    """Return the stage's name, and what Odoo.sh calls it when the library does not know it."""
    return branch.stage_name if branch.stage is odouche.Stage.UNKNOWN else branch.stage.value


def is_given(ctx: typer.Context, parameter: str) -> bool:
    """Tell whether the user gave that parameter, by a flag or a variable."""
    source = ctx.get_parameter_source(parameter)
    return source is not None and source.name != "DEFAULT"


def _refuse_empty(ctx: typer.Context, value: str, flag: str) -> None:
    """Fail on an empty flag, which a script's unset variable gives, and never fall back from it."""
    if not value:
        msg = f"{flag} is empty."
        raise ContextError(msg, ctx=ctx)


def _source(ctx: typer.Context, parameter: str, flag: str, variable: str) -> str:
    """Name the source of a value the user gave: the flag or the variable."""
    source = ctx.get_parameter_source(parameter)
    return variable if source is not None and source.name == "ENVIRONMENT" else flag


def _debug(ctx: typer.Context, line: str) -> None:
    """Say on stderr which source was used, under `--debug`."""
    if ctx.find_root().params.get("debug"):
        typer.echo(line, err=True)


def checkout_branch() -> str | None:
    """Return the current branch, or `None` on a detached HEAD or outside a repository."""
    return _git("branch", "--show-current")


def checkout_head() -> str | None:
    """Return the full hash of the commit checked out, or `None` outside a repository or on a branch with no commit."""
    return _git("rev-parse", "--verify", "--quiet", "HEAD")


def checkout_is_of(project: str, projects: Callable[[], Iterable[odouche.Project]]) -> bool:
    """Tell whether the checkout's repository is the one the project of that name builds.

    `projects` is called only from a checkout that has a GitHub remote.
    """
    repository = _checkout_repository()
    if repository is None:
        return False
    return any(found.name == project and found.repository.casefold() == repository.casefold() for found in projects())


def _checkout_repository() -> str | None:
    """Return the GitHub repository of the current branch's remote, or of `origin`, as `owner/name`."""
    branch = checkout_branch()
    remote = _git("config", "--get", f"branch.{branch}.remote") if branch else None
    # `.` is a branch that tracks a local one.
    if remote is None or remote == ".":
        remote = "origin"
    url = _git("remote", "get-url", remote)
    return _github_repository(url) if url else None


def _github_repository(url: str) -> str | None:
    """Return `owner/name` from a GitHub remote, SSH or HTTPS, and nothing else of the address."""
    found = _GITHUB.fullmatch(url)
    return f"{found['owner']}/{found['name']}" if found else None


def _git(*args: str) -> str | None:
    """Return what a git command prints, or `None` when it fails, is not installed or is too slow."""
    try:
        done = subprocess.run(  # noqa: S603 - an argument list, no shell
            ["git", *args],  # noqa: S607 - the user's own git
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None
