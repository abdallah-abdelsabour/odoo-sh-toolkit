"""What the library returns: its own models, not the payloads of Odoo.sh."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from odouche.errors import OdoucheError


@dataclass(frozen=True, slots=True)
class Project:
    """An Odoo.sh project the session's user can reach."""

    id: int
    """The number Odoo.sh gives the project."""

    name: str
    """The project's name on Odoo.sh, as in the address of its page."""

    repository: str
    """The GitHub repository the project builds, as `owner/name`."""

    url: str
    """The address of the project's page on Odoo.sh."""


class Stage(StrEnum):
    """The stage a branch sits in on Odoo.sh."""

    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    UNKNOWN = "unknown"
    """A stage the library does not know. The branch's `stage_name` holds what Odoo.sh calls it."""


@dataclass(frozen=True, slots=True)
class Branch:
    """A branch of an Odoo.sh project."""

    id: int
    """The number Odoo.sh gives the branch."""

    name: str
    """The git branch."""

    stage: Stage
    """The stage the branch sits in now."""

    stage_name: str
    """What Odoo.sh calls that stage, such as `dev`."""


class BuildStatus(StrEnum):
    """Where a build is in its life on Odoo.sh. How it ended is its `BuildResult`."""

    PROGRESS = "progress"
    UPDATING = "updating"
    DONE = "done"
    DROPPED = "dropped"
    """What a build becomes when a newer one replaces it."""
    SKIPPED = "skipped"
    KILLED = "killed"
    UNKNOWN = "unknown"
    """A status the library does not know. The build's `status_name` holds what Odoo.sh calls it."""


class BuildResult(StrEnum):
    """How a build ended."""

    SUCCESS = "success"
    FAILED = "failed"
    WARNING = "warning"
    UNKNOWN = "unknown"
    """A result the library does not know. The build's `result_name` holds what Odoo.sh calls it."""


_FINISHED = frozenset({BuildStatus.DONE, BuildStatus.DROPPED, BuildStatus.SKIPPED, BuildStatus.KILLED})


@dataclass(frozen=True, slots=True)
class Commit:
    """The commit a build was made from, as Odoo.sh reports it."""

    hash: str
    """The commit's full hash."""

    message: str
    """The whole commit message."""

    author: str
    """The author's name. It is another person's personal data, carried as Odoo.sh gives it."""

    timestamp: datetime
    """When the commit was made, in UTC."""

    url: str
    """The address of the commit on GitHub."""


@dataclass(frozen=True, slots=True)
class Build:
    """What Odoo.sh made of a commit on a branch."""

    id: int
    """The number Odoo.sh gives the build."""

    name: str
    """The build's name on Odoo.sh, which is not derivable from its branch."""

    branch_id: int
    """The number of the branch the build belongs to."""

    branch_name: str
    """The git branch."""

    commit: Commit
    """The commit the build was made from."""

    status: BuildStatus
    """Where the build is in its life."""

    status_name: str
    """What Odoo.sh calls that status, such as `progress`."""

    result: BuildResult | None
    """How the build ended, or `None` when Odoo.sh gives none. `finished` says whether it ended."""

    result_name: str | None
    """What Odoo.sh calls that result, such as `success`."""

    status_info: str | None
    """What the build is doing, in Odoo.sh's own words, such as `Installing: account`."""

    started_at: datetime | None
    """When a worker took the build, in UTC, or `None` while it waits for one."""

    url: str | None
    """The address of the build's database, when it has one."""

    @property
    def finished(self) -> bool:
        """Whether the build has ended, whatever its result.

        `DONE` and `DROPPED` are the ended statuses Odoo.sh has been seen to give. `SKIPPED` and
        `KILLED` are counted as ended from their names alone. An unknown status is not finished.
        """
        return self.status in _FINISHED


@dataclass(frozen=True, slots=True)
class SshTarget:
    """Where `ssh` reaches a build: the user to log in as and the host."""

    user: str
    """The user on the build's host, which is the build's number."""

    host: str
    """The build's own host."""


@dataclass(frozen=True, slots=True)
class Firewall:
    """The IPv4 addresses a project's firewall lets reach its production and staging builds.

    The firewall covers SSH, and PostgreSQL on a dedicated server. A build still asks for valid
    credentials. Odoo.sh can take up to 5 minutes to apply a change.
    """

    project: str
    """The project's name on Odoo.sh."""

    addresses: tuple[str, ...]
    """The addresses, and ranges in CIDR notation, in the order Odoo.sh keeps them."""


class LogKind(StrEnum):
    """The logs Odoo.sh keeps of a build. A build has only some of them."""

    INSTALL = "install"
    PIP = "pip"
    ODOO = "odoo"
    UPDATE = "update"
    NEUTRALIZE = "neutralize"
    UPGRADE = "upgrade"
    UNKNOWN = "unknown"
    """A log the library does not know. The log's `name` holds what Odoo.sh calls it."""


@dataclass(frozen=True, slots=True)
class Log:
    """One of the logs a build has."""

    kind: LogKind
    """Which log it is."""

    name: str
    """What Odoo.sh calls it, such as `install`."""

    modified_at: datetime
    """When it was last written to, in UTC."""

    size: str
    """Its size in Odoo.sh's own words, such as `156 KB`."""


@dataclass(frozen=True, slots=True)
class LogLine:
    """A line of a build's log. It is untrusted: whatever a process printed, unchanged."""

    text: str
    """The line without its newline. Bytes that are not UTF-8 are replaced."""

    offset: int
    """The byte just past the line, which is where a follow can start again."""

    truncated: bool
    """Whether the line was longer than the library reads, and was cut."""


class SessionSource(StrEnum):
    """Where a session came from."""

    ARGUMENT = "argument"
    """The caller passed it."""
    ENVIRONMENT = "environment"
    KEYRING = "keyring"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """What is known of a session without asking Odoo.sh. It never holds the session."""

    source: SessionSource
    """Where the session came from."""

    stored_at: datetime | None
    """When the login stored it, in UTC, or `None` when it is not the stored one."""

    expires_at: datetime | None
    """When it passes the client-side max age, in UTC, or `None` when it is not the stored one."""


@dataclass(frozen=True, slots=True)
class Identity:
    """The user a session belongs to, as Odoo.sh reports them."""

    user_id: int
    """The number Odoo.sh gives the user."""

    name: str | None
    """The user's display name, when Odoo.sh gives one."""

    username: str
    """The user's GitHub login."""

    email: str | None
    """The user's email address, when Odoo.sh gives one. It is personal data, carried as given."""

    session: SessionInfo
    """The session that was asked with."""


@dataclass(frozen=True, slots=True)
class LogoutResult:
    """What a logout did."""

    source: SessionSource | None
    """Where the session came from, or `None` when there was none to log out of."""

    deleted: bool
    """Whether the stored session was deleted. One passed or set in the environment never is."""

    invalidated: bool
    """Whether Odoo.sh has ended the session, so that a copy of it no longer works."""

    failure: OdoucheError | None
    """Why Odoo.sh could not be asked to end the session, or `None`."""
