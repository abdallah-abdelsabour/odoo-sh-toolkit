"""The client: the one object a caller asks Odoo.sh through."""

from collections.abc import Callable, Generator
from typing import Self

from odouche._session import SessionStore
from odouche._upstream import branches, builds, firewall, logs, projects, rebuild, ssh, user, watch
from odouche._upstream.transport import Transport
from odouche.models import Branch, Build, Firewall, Identity, Log, LogKind, LogLine, Project, SessionInfo, SshTarget
from odouche.secret import Secret


# What the tests replace: the connection to Odoo.sh.
_transport: Callable[..., Transport] = Transport


class Client:
    """Asks Odoo.sh on behalf of one session.

    With no argument the session is the one in the environment, then the one stored by `login`.
    A tool that keeps sessions itself passes its own, which is never stored. Raises
    `NoSessionError` when there is none, `SessionExpiredError` when the stored one has passed its
    max age and `KeyringUnavailableError` when the keyring cannot be read.

    Every call raises `SessionExpiredError` when Odoo.sh rejects the session, after deleting it if
    it is the stored one. Nothing is cached: each call asks Odoo.sh. Use the client as a context
    manager, or call `close`.

    With `read_only`, a call that changes state on Odoo.sh raises `ReadOnlyError` and sends nothing.
    """

    def __init__(self, session: Secret | None = None, *, read_only: bool = False) -> None:
        store = SessionStore(session)
        resolved = store.load()
        self._session = SessionInfo(resolved.source, resolved.stored_at, resolved.expires_at)
        self._transport = _transport(resolved.session, on_rejected=store.discard, read_only=read_only)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the connections."""
        self._transport.close()

    @property
    def session(self) -> SessionInfo:
        """Where the session came from and when it passes its max age. Odoo.sh is not asked."""
        return self._session

    @property
    def read_only(self) -> bool:
        """Whether the client refuses the calls that change state on Odoo.sh."""
        return self._transport.read_only

    def identity(self) -> Identity:
        """Return the user the session belongs to, which also tells that Odoo.sh still accepts it."""
        return user.identity(self._transport, self._session)

    def projects(self) -> list[Project]:
        """List the projects the session's user can reach."""
        return projects.projects(self._transport)

    def branches(self, project: Project | str) -> list[Branch]:
        """List the branches of a project, given as a `Project` or by its name.

        They come in the order Odoo.sh answers them, which is not by name or by stage. Raises
        `NotFoundError` when the project is not among those the session's user can reach, whether
        or not it exists, and `PermissionDeniedError` when Odoo.sh lists it but refuses its
        branches. Asks Odoo.sh twice, for the projects and then for the branches.
        """
        return branches.branches(self._transport, _project_name(project))

    def builds(self, branch: Branch | int, *, limit: int = builds.DEFAULT_LIMIT) -> list[Build]:
        """List the latest builds of a branch, given as a `Branch` or by its number, newest first.

        At most `limit` builds are returned, in one request. Odoo.sh may answer fewer than asked
        for: it has only been seen to answer up to four, and older builds are out of reach. Raises
        `NotFoundError` when the branch is not one the session's user can reach.
        """
        return builds.builds(self._transport, _branch_id(branch), limit)

    def build(self, branch: Branch | int, build_id: int) -> Build:
        """Return one build of a branch by its number.

        Odoo.sh has no request for a build by its number, so the build is looked for among the
        branch's latest. Raises `NotFoundError` when it is not there, which an older build is not.
        """
        return builds.build(self._transport, _branch_id(branch), build_id)

    def latest_build(self, branch: Branch | int) -> Build | None:
        """Return the newest build of a branch, or `None` when it has none."""
        latest = builds.builds(self._transport, _branch_id(branch), 1)
        return latest[0] if latest else None

    def ssh_target(self, build: Build) -> SshTarget:
        """Return the user and the host `ssh` reaches a build with. Odoo.sh is not asked.

        Odoo.sh accepts there the keys registered on the user's account, not the session. Raises
        `NotFoundError` when the build has no host, and `UpstreamChangedError` when its number or
        its address is not one that can be handed to `ssh` as it is.
        """
        return ssh.target(build)

    def rebuild(self, branch: Branch | int) -> Build:
        """Change state on Odoo.sh: start a new build of a branch, given as a `Branch` or by its number.

        Returns the new build, which replaces the branch's latest one. Only a development or a
        staging branch is rebuilt: any other raises `StageRefusedError` before the request.

        The request is sent once. Raises `OutcomeUnknownError` when it left and Odoo.sh did not
        confirm it, or the new build cannot be found: look at the branch before calling again,
        since a second call can start a second build. Raises `ReadOnlyError` on a read-only
        client, and `NotFoundError` when the branch is not one the session's user can reach.
        A branch that is not a number raises `TypeError`.
        """
        stage = branch.stage if isinstance(branch, Branch) else None
        return rebuild.rebuild(self._transport, _branch_id(branch), stage)

    def check_rebuild(self, branch: Branch) -> None:
        """Raise `StageRefusedError` when `rebuild` would refuse the branch for its stage.

        Odoo.sh is not asked and nothing changes, on a read-only client too.
        """
        rebuild.allow(branch.stage)

    def firewall(self, project: Project | str) -> Firewall:
        """Return the IPv4 addresses a project's firewall lets reach its production and staging builds.

        Raises `NotFoundError` when the project is not among those the session's user can reach,
        and `PermissionDeniedError` when Odoo.sh lists it but refuses its settings.
        """
        return firewall.firewall(self._transport, _project_name(project))

    def plan_allow_ip(self, current: Firewall, address: str, *, replace: bool = True) -> Firewall:
        """Return the firewall `allow_ip` would leave, from the `current` one. Odoo.sh is not asked.

        With `replace`, the address is the only one left; without, it is added to the others. It
        can be a range in CIDR notation. Raises `ValueError` when `address` is not a public IPv4
        address or range, and `FirewallRefusedError` when the firewall would hold more than the 10
        entries Odoo.sh takes.
        """
        return firewall.plan(current, address, replace=replace)

    def allow_ip(self, project: Project | str, address: str, *, replace: bool = True) -> Firewall:
        """Change state on Odoo.sh: let an IPv4 address through a project's firewall, and return it.

        With `replace`, the default, the address becomes the only one; without, it is added to the
        others. Odoo.sh can take up to 5 minutes to apply it. Nothing is sent when the firewall is
        already what it would be.

        The request is sent once. Raises `FirewallRefusedError` when Odoo.sh refuses an entry,
        which changes nothing, and `OutcomeUnknownError` when it left and Odoo.sh did not answer
        the addresses it was given: look at the firewall before calling again. Raises the errors of
        `plan_allow_ip` before anything is sent, `ReadOnlyError` on a read-only client, and
        `NotFoundError` when the project is not one the session's user can reach.
        """
        return firewall.allow(self._transport, _project_name(project), address, replace=replace)

    def watch_build(
        self, project: Project | str, build: Build, *, timeout: float, pulse: float | None = None
    ) -> Generator[Build]:
        """Yield a build of a project as it is now, then at each change, and end once it has finished.

        A change is one of `status`, `result` or `status_info`. The last build yielded is the
        finished one, and a build that has already finished is yielded once. The watch stays on
        this build: a newer one on the branch ends it as `DROPPED`. Closing the iterator closes the
        connection.

        With `pulse`, the build is also yielded unchanged, every `pulse` seconds at least while
        nothing changes. An iterator is closed only between two builds, so a caller that may have
        to stop the watch asks for one.

        Raises `StreamTimeoutError` after `timeout` seconds, `UpstreamUnavailableError` when the
        connection fails three times in a row or Odoo.sh cannot be asked for the build, and
        `NotFoundError` when the build is not among its
        branch's latest, or the project is not one the session's user can reach.
        """
        return watch.watch(self._transport, project, build, timeout=timeout, pulse=pulse)

    def logs(self, project: Project | str, build: Build) -> list[Log]:
        """List the logs a build of a project has, which is none while it waits for a worker.

        Raises `NotFoundError` when the build is not among its branch's latest, or the project is
        not one the session's user can reach.
        """
        return logs.logs(self._transport, _project_name(project), build)

    def read_log(
        self, project: Project | str, build: Build, kind: LogKind | str, *, tail: int | None = None
    ) -> Generator[LogLine]:
        """Yield the lines of one log of a build, given as a `LogKind` or by its name.

        Log content is untrusted: it is whatever a process printed, terminal escape sequences and
        secrets of the instance included, and it is returned unchanged.

        With `tail`, only the last lines are yielded, out of the log's last mebibyte. A line
        longer than 64 KiB is cut. Closing the iterator closes the connection. Raises
        `NotFoundError` when the build has no such log.
        """
        return logs.read(self._transport, _project_name(project), build, kind, tail)

    def follow_log(
        self,
        project: Project | str,
        build: Build,
        kind: LogKind | str,
        *,
        timeout: float,
        tail: int = 0,
        offset: int | None = None,
    ) -> Generator[LogLine]:
        """Yield the lines of one log of a build as they are written, until the iterator is closed.

        Log content is untrusted, as in `read_log`.

        It starts after the log's last `tail` lines, or at `offset`, which is the `offset` of a
        line read before. Odoo.sh is asked every second. Raises `StreamTimeoutError` after
        `timeout` seconds, which `math.inf` makes no limit, `UpstreamUnavailableError` when a
        failed request is not answered after two more tries, and `NotFoundError` when the build
        has no such log.
        """
        return logs.follow(
            self._transport, _project_name(project), build, kind, timeout=timeout, tail=tail, offset=offset
        )


def _project_name(project: Project | str) -> str:
    return project.name if isinstance(project, Project) else project


def _branch_id(branch: Branch | int) -> int:
    return branch.id if isinstance(branch, Branch) else branch
