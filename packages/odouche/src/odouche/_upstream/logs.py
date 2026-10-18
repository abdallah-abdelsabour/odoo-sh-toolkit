"""The logs of a build, which a worker serves and the project's token opens.

Nothing of a log goes to the library's logger.
"""

import re
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from itertools import islice

from odouche._upstream import builds, projects
from odouche._upstream.reader import Reader
from odouche._upstream.transport import Part, Transport
from odouche.errors import NotFoundError, StreamTimeoutError, UpstreamChangedError, UpstreamUnavailableError
from odouche.models import Build, Log, LogKind, LogLine
from odouche.secret import Secret


# What the page asks for when it opens a log.
TAIL_BYTES = 1048576
MAX_LINE = 65536

_OPERATION = "logs"
_KINDS = {kind.value: kind for kind in LogKind if kind is not LogKind.UNKNOWN}
_NAME = re.compile(r"[a-z0-9_-]+")
# The page's own.
_INTERVAL = 1.0
_RETRIES = (1.0, 2.0)

# What the tests replace: the clock and the wait between two requests.
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True, slots=True)
class _Source:
    """Where the logs of one build are asked for, and with what."""

    worker: str
    token: Secret
    build_id: int

    def path(self, name: str) -> str:
        return f"/paas/build/{self.build_id}/logs/{name}"


@dataclass(frozen=True, slots=True)
class _File:
    """One log of a build."""

    transport: Transport
    source: _Source
    path: str

    def open(self, byte_range: str) -> AbstractContextManager[Part | None]:
        return self.transport.worker_read(_OPERATION, self.source.worker, self.path, self.source.token, byte_range)


class _Lines:
    """Cuts what arrives into lines, and holds no more than `MAX_LINE` of the one not ended yet."""

    def __init__(self) -> None:
        self.consumed = 0
        self._held = bytearray()
        # Whether what comes before the next newline belongs to a line that is not yielded.
        self._skipping = False

    def restart(self, consumed: int, *, skipping: bool = False) -> None:
        """Start at that byte of the log, inside a line that is not yielded when `skipping`."""
        self.consumed = consumed
        self._held.clear()
        self._skipping = skipping

    def feed(self, chunk: bytes) -> Iterator[LogLine]:
        *ended, rest = chunk.split(b"\n")
        for piece in ended:
            self.consumed += len(piece) + 1
            if self._skipping:
                self._skipping = False
            else:
                self._hold(piece)
                yield self._line()
        self.consumed += len(rest)
        if not self._skipping:
            self._hold(rest)

    def flush(self) -> Iterator[LogLine]:
        """Yield the line the log ends with, when no newline ends it."""
        if self._held:
            yield self._line()

    def _hold(self, piece: bytes) -> None:
        # One byte past the maximum tells a line that was cut.
        self._held += piece[: MAX_LINE + 1 - len(self._held)]

    def _line(self) -> LogLine:
        text = bytes(self._held[:MAX_LINE]).decode("utf-8", errors="replace")
        line = LogLine(text=text, offset=self.consumed, truncated=len(self._held) > MAX_LINE)
        self._held.clear()
        return line


def logs(transport: Transport, project: str, build: Build) -> list[Log]:
    """List the logs a build has, which is none while it waits for a worker."""
    source = _source(transport, project, build)
    return [] if source is None else _logs(transport, source)


def read(transport: Transport, project: str, build: Build, kind: LogKind | str, tail: int | None) -> Generator[LogLine]:
    """Yield the lines of a log, or the last `tail` of them that its last mebibyte holds."""
    if tail is not None and builds.number("tail", tail) < 1:
        raise ValueError("A tail is at least 1")
    return _read(_locate(transport, project, build, kind), tail)


def follow(
    transport: Transport,
    project: str,
    build: Build,
    kind: LogKind | str,
    *,
    timeout: float,
    tail: int,
    offset: int | None,
) -> Generator[LogLine]:
    """Yield the lines of a log as they are written, from `offset` or after the last `tail` lines."""
    if timeout <= 0:
        raise ValueError("A timeout is more than 0")
    if builds.number("tail", tail) < 0:
        raise ValueError("A tail is at least 0")
    if offset is not None and builds.number("offset", offset) < 0:
        raise ValueError("An offset is at least 0")
    deadline = _monotonic() + timeout
    return _follow(_locate(transport, project, build, kind), deadline, tail, offset)


def _source(transport: Transport, project: str, build: Build) -> _Source | None:
    worker = builds.worker(transport, build.branch_id, build.id)
    if worker is None:
        return None
    return _Source(worker, projects.access_token(transport, project), build.id)


def _logs(transport: Transport, source: _Source) -> list[Log]:
    answer = Reader(_OPERATION, transport.worker_call(_OPERATION, source.worker, source.path("list"), source.token))
    return [
        Log(
            kind=_KINDS.get(log.text("name"), LogKind.UNKNOWN),
            name=log.text("name"),
            modified_at=builds.timestamp(log, "write_date", log.text("write_date")),
            size=log.text("size"),
        )
        for log in answer.items("result")
    ]


def _locate(transport: Transport, project: str, build: Build, kind: LogKind | str) -> _File:
    """Return where a log is read. Its name goes into the address, so it is one the worker listed."""
    name = kind.value if isinstance(kind, LogKind) else kind
    source = _source(transport, project, build)
    if source is None:
        raise NotFoundError(f"Build {build.id} has no log yet: it waits for a worker.", operation=_OPERATION)
    if name not in [log.name for log in _logs(transport, source)]:
        raise NotFoundError(f"Build {build.id} has no log named {name!r}.", operation=_OPERATION)
    if _NAME.fullmatch(name) is None:
        raise UpstreamChangedError(_OPERATION, "result.name")
    return _File(transport, source, source.path(name))


def _read(file: _File, tail: int | None) -> Generator[LogLine]:
    if tail is None:
        lines = _Lines()
        yield from _fetch(file, lines)
        yield from lines.flush()
    else:
        yield from _last(*_tail(file, tail), tail, _Lines(), ended=True)


def _follow(file: _File, deadline: float, tail: int, offset: int | None) -> Generator[LogLine]:
    lines = _Lines()
    if offset is None:
        yield from _last(*_tail(file, tail), tail, lines, ended=False)
    else:
        lines.restart(offset)
        yield from _fetch(file, lines)
    while True:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise StreamTimeoutError("The log was still being followed at the timeout.", operation=_OPERATION)
        _sleep(min(_INTERVAL, remaining))
        yield from _fetch(file, lines)


def _tail(file: _File, count: int) -> tuple[bytes, int]:
    """Read the end of a log: its bytes, and where the first of them is in the log."""
    for delay in (*_RETRIES, None):
        try:
            return _suffix(file, count)
        except UpstreamUnavailableError:
            if delay is None:
                raise
            _sleep(delay)
    raise AssertionError


def _suffix(file: _File, count: int) -> tuple[bytes, int]:
    # With no line to keep, the last byte tells where the log ends.
    byte_range = f"bytes=-{TAIL_BYTES if count else 1}"
    with file.open(byte_range) as part:
        if part is None:
            return b"", 0
        held = bytearray()
        first = part.first
        for chunk in part.chunks:
            held += chunk
            # A part longer than asked for is held to its end.
            extra = len(held) - TAIL_BYTES
            if extra > 0:
                del held[:extra]
                first += extra
    return bytes(held), first


def _last(held: bytes, first: int, count: int, lines: _Lines, *, ended: bool) -> Iterator[LogLine]:
    """Yield the last `count` lines of the end of a log, and leave `lines` where it ends.

    The lines are made one at a time: an end of only newlines is a mebibyte of them.
    """
    total = sum(1 for _ in _cut(held, first, _Lines(), ended=ended))
    return islice(_cut(held, first, lines, ended=ended), max(total - count, 0), None)


def _cut(held: bytes, first: int, lines: _Lines, *, ended: bool) -> Iterator[LogLine]:
    # An end that starts inside the log starts inside a line.
    lines.restart(first, skipping=first > 0)
    for start in range(0, len(held), MAX_LINE):
        yield from lines.feed(held[start : start + MAX_LINE])
    if ended:
        yield from lines.flush()


def _fetch(file: _File, lines: _Lines) -> Iterator[LogLine]:
    """Yield what the log holds past `lines`, starting again where a failed request stopped."""
    failures = 0
    while True:
        before = lines.consumed
        try:
            yield from _part(file, lines)
        except UpstreamUnavailableError:
            if lines.consumed > before:
                failures = 0
            if failures == len(_RETRIES):
                raise
            _sleep(_RETRIES[failures])
            failures += 1
        else:
            return


def _part(file: _File, lines: _Lines) -> Iterator[LogLine]:
    # As the page does, ask from the last byte held, not the one after.
    first = max(lines.consumed - 1, 0)
    held = lines.consumed - first
    with file.open(f"bytes={first}-") as part:
        if part is None:
            return
        if part.first != first:
            raise UpstreamChangedError(_OPERATION, "Content-Range")
        for chunk in part.chunks:
            new = chunk[held:]
            held -= len(chunk) - len(new)
            yield from lines.feed(new)
