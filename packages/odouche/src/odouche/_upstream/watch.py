"""Watching a build: the bus socket says what changes, and the builds request what the socket cannot."""

import json
import time
from collections.abc import Callable, Generator, Iterator

from odouche._upstream import builds, projects
from odouche._upstream.reader import Reader
from odouche._upstream.transport import Socket, Transport
from odouche.errors import NotFoundError, StreamTimeoutError, UpstreamChangedError, UpstreamUnavailableError
from odouche.models import Build, Project


_OPERATION = "watch"
_EVENT = "paas.repository/build_event"
# How long nothing is heard of the build before Odoo.sh is asked. It is the page's idle interval.
_QUIET = 60.0
_RETRIES = (1.0, 2.0)

# What the tests replace: the clock and the wait before the socket is opened again.
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


def watch(
    transport: Transport, project: Project | str, build: Build, *, timeout: float, pulse: float | None = None
) -> Generator[Build]:
    """Yield a build as it is now, then at each change, and end after yielding it finished.

    With `pulse`, it is also yielded unchanged when a wait of that long at most brought no change.
    """
    if timeout <= 0:
        raise ValueError("A timeout is more than 0")
    if pulse is not None and pulse <= 0:
        raise ValueError("A pulse is more than 0")
    return _Watch(transport, build, _monotonic() + timeout, pulse).run(project)


class _Watch:
    """One watch of one build: what was last yielded of it, and what was last heard of it."""

    def __init__(self, transport: Transport, build: Build, deadline: float, pulse: float | None) -> None:
        self._transport = transport
        self._build = build
        self._deadline = deadline
        self._pulse = pulse
        self._heard_at = 0.0
        # The last notification held, which a subscription names.
        self._last = 0
        self._failures = 0

    def run(self, project: Project | str) -> Generator[Build]:
        failed = None
        try:
            yield from self._run(project)
        except UpstreamUnavailableError as error:
            failed = error
        if failed is not None:
            # A request the timeout cut short is the timeout, which `_remaining` raises.
            self._remaining()
            raise failed

    def _run(self, project: Project | str) -> Generator[Build]:
        self._heard_at = _monotonic()
        self._build = self._asked()
        yield self._build
        if self._build.finished:
            return
        channel = f"paas_repository:{_project_id(self._transport, project, self._remaining())}"
        while not self._build.finished:
            self._remaining()
            try:
                with self._transport.bus(_OPERATION, self._remaining()) as socket:
                    yield from self._listen(socket, channel)
                continue
            except UpstreamUnavailableError as error:
                # The builds request has been sent again already.
                if error.operation != _OPERATION or self._failures == len(_RETRIES):
                    raise
            self._failures += 1
            _sleep(min(_RETRIES[self._failures - 1], self._remaining()))

    def _listen(self, socket: Socket, channel: str) -> Iterator[Build]:
        socket.send(json.dumps({"event_name": "subscribe", "data": {"channels": [channel], "last": self._last}}))
        # The socket says nothing of what happened before it was open, or of a rejected session.
        yield from self._ask()
        while not self._build.finished:
            quiet = self._heard_at + _QUIET - _monotonic()
            if quiet <= 0:
                socket.idle()
                yield from self._ask()
                self._failures = 0
                continue
            frame = socket.receive(min(quiet, self._remaining(), self._pulse or quiet))
            changed = False
            if frame is not None:
                self._failures = 0
                for change in self._events(frame):
                    changed = True
                    yield change
            # A frame can hold no change: it is of another build, or says what is known.
            if self._pulse and not changed:
                yield self._build

    def _remaining(self) -> float:
        remaining = self._deadline - _monotonic()
        if remaining <= 0:
            raise StreamTimeoutError("The build was still being watched at the timeout.", operation=_OPERATION)
        return remaining

    def _ask(self) -> Iterator[Build]:
        yield from self._take(self._asked())

    def _asked(self) -> Build:
        return builds.build(self._transport, self._build.branch_id, self._build.id, within=self._remaining())

    def _events(self, frame: str) -> Iterator[Build]:
        for notification in _notifications(frame):
            self._last = max(self._last, notification.integer("id"))
            message = notification.child("message")
            if message.text("type") != _EVENT:
                continue
            payload = message.child("payload")
            if payload.integer("build_id") != self._build.id or self._build.finished:
                continue
            values = payload.child("values")
            yield from self._take(builds.whole(values) if values.has("name") else builds.short(self._build, values))

    def _take(self, build: Build) -> Iterator[Build]:
        self._heard_at = _monotonic()
        changed = _state(build) != _state(self._build)
        self._build = build
        if changed:
            yield build


def _state(build: Build) -> tuple[str, str | None, str | None]:
    return build.status_name, build.result_name, build.status_info


def _notifications(frame: str) -> list[Reader]:
    try:
        notifications = json.loads(frame)
    except ValueError:
        notifications = None
    # Raised out of the handler: the exception handled there holds the frame.
    if not isinstance(notifications, list):
        raise UpstreamChangedError(_OPERATION, "frame")
    return Reader(_OPERATION, {"frame": notifications}).items("frame")


def _project_id(transport: Transport, project: Project | str, within: float) -> int:
    if isinstance(project, Project):
        return project.id
    found = projects.project_id(transport, project, within=within)
    if found is None:
        raise NotFoundError(f"The session's user can reach no project named {project!r}.", operation=_OPERATION)
    return found
