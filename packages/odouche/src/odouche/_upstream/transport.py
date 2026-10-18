"""The one place a request to Odoo.sh is sent, and the session attached to it."""

import json
import logging
import re
import socket
import threading
import time
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from functools import wraps
from http.cookiejar import DefaultCookiePolicy
from importlib.metadata import version
from typing import Concatenate, Self, cast

import httpx2
from httpx2.websockets import (
    WebSocketDisconnect,
    WebSocketInvalidTypeReceived,
    WebSocketNetworkError,
    WebSocketSession,
    WebSocketUpgradeError,
)
from wsproto.events import CloseConnection
from wsproto.utilities import LocalProtocolError

from odouche.errors import (
    NotFoundError,
    OdoucheError,
    OutcomeUnknownError,
    PermissionDeniedError,
    ReadOnlyError,
    SessionExpiredError,
    UpstreamChangedError,
    UpstreamUnavailableError,
)
from odouche.secret import Secret


HOST = "www.odoo.sh"

_LOGIN_PATH = "/web/login"
_BUS_PATH = "/websocket"
# The `v` of the page's worker script. With another, the socket opens and sends nothing.
_BUS_VERSION = "18.0-7"
# One frame can hold several whole builds.
_BUS_MESSAGE = 1048576
_SESSION_EXPIRED = "odoo.http.SessionExpiredException"
_ACCESS_DENIED = "odoo.exceptions.AccessError"
_UNEXPLAINED = "builtins.Exception"
# What Odoo raises for a value it does not take: the change is rolled back.
_INVALID = frozenset({"odoo.exceptions.ValidationError", "odoo.exceptions.UserError"})
_SESSION_ALPHABET = re.compile(r"[A-Za-z0-9_-]+")
_WORKER = re.compile(r"https://[a-z0-9-]+\.odoo\.com")
_CONTENT_RANGE = re.compile(r"bytes (\d+)-\d+/\d+")
_PHASE = 10.0
_READ = 30.0
_TIMEOUT = httpx2.Timeout(connect=_PHASE, read=_READ, write=_PHASE, pool=_PHASE)
_BACKOFF = (1.0, 2.0)
_TRANSIENT = frozenset({502, 503, 504})
# The failures of a connection that was never opened: the request did not leave.
_NOT_SENT = frozenset({"ConnectError", "ConnectTimeout", "PoolTimeout"})
_DENIED = "Odoo.sh does not allow this to the session's user."
_REFUSED: dict[int, tuple[type[OdoucheError], str]] = {
    404: (NotFoundError, "Odoo.sh has nothing at this address."),
    403: (PermissionDeniedError, _DENIED),
}

_logger = logging.getLogger("odouche")


class _WithoutQuery(logging.Filter):
    """Cuts the query from the address the HTTP client logs: a worker takes the token there."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_with(query=None) if isinstance(arg, httpx2.URL) else arg for arg in record.args
            )
        return True


logging.getLogger("httpx2").addFilter(_WithoutQuery())


@dataclass(frozen=True, slots=True)
class _Answer:
    """What is kept of one attempt: nothing of the request, and of the response only what is read."""

    status: int | None = None
    location: str | None = None
    body: object = None
    failure: str | None = None

    @property
    def transient(self) -> bool:
        return self.status is None or self.status in _TRANSIENT


@dataclass(frozen=True, slots=True)
class Part:
    """The part of a worker's file that one ranged request was answered with."""

    first: int
    """Where its first byte is in the file."""

    chunks: Iterator[bytes]
    """Its bytes as they arrive. Raises `UpstreamUnavailableError` when the connection drops."""


class Socket:
    """The bus socket, once Odoo.sh has opened it. It gives no sign of a missing or expired session."""

    def __init__(self, operation: str, session: WebSocketSession) -> None:
        self._operation = operation
        self._session = session

    def send(self, text: str) -> None:
        """Send one text frame."""
        self._write(lambda: self._session.send_text(text))

    def idle(self) -> None:
        """Send the frame the page sends when it has nothing to say."""
        self._write(lambda: self._session.send_bytes(b"\x00"))

    def receive(self, timeout: float) -> str | None:
        """Return the next text frame, or `None` when none came in `timeout` seconds.

        Raises `UpstreamUnavailableError` when the socket is closed or drops.
        """
        try:
            return self._session.receive_text(timeout)
        except (TimeoutError, WebSocketInvalidTypeReceived):
            return None
        except (WebSocketDisconnect, WebSocketNetworkError) as error:
            failure = type(error).__name__
        raise _unavailable(self._operation, failure)

    def _write(self, write: Callable[[], None]) -> None:
        try:
            write()
        # A socket that has closed refuses the frame before it is written.
        except (WebSocketNetworkError, LocalProtocolError) as error:
            failure = type(error).__name__
        else:
            return
        raise _unavailable(self._operation, failure)


def is_unauthenticated(status: int, location: str | None, body: object) -> bool:
    """Tell whether an answer is one of the two Odoo.sh gives to a missing or expired session."""
    if status == httpx2.codes.SEE_OTHER:
        return _lands_on(location, _LOGIN_PATH)
    return status == httpx2.codes.OK and _error_name(body) == _SESSION_EXPIRED


def is_sendable(session: Secret) -> bool:
    """Tell whether a value has the alphabet of a session, so that it is a cookie value and no more."""
    return _SESSION_ALPHABET.fullmatch(session.expose_secret()) is not None


def _error_name(body: object) -> object:
    """Return the name of the exception an answer reports, when it reports one."""
    error = cast("dict[str, object]", body).get("error") if isinstance(body, dict) else None
    data = cast("dict[str, object]", error).get("data") if isinstance(error, dict) else None
    return cast("dict[str, object]", data).get("name") if isinstance(data, dict) else None


def _lands_on(location: str | None, path: str) -> bool:
    """Tell whether a redirect sends the browser to a path of Odoo.sh."""
    if location is None:
        return False
    try:
        url = httpx2.URL(f"https://{HOST}/").join(location)
    except httpx2.InvalidURL:
        return False
    return (url.scheme, url.host, url.port, url.path) == ("https", HOST, None, path)


def _url(path: str) -> httpx2.URL:
    url = httpx2.URL(f"https://{HOST}{path}")
    if not path.startswith("/") or path.startswith("//") or url.scheme != "https" or url.host != HOST:
        raise ValueError(f"A request path is absolute and on {HOST}")
    return url


def _worker_url(operation: str, worker: str, path: str) -> httpx2.URL:
    """Return an address on a worker, which is a host of `odoo.com` that Odoo.sh named."""
    if _WORKER.fullmatch(worker) is None:
        raise UpstreamChangedError(operation, "worker_url")
    # The host is compared as the client reads it, which is not always as it is written.
    if f"https://{httpx2.URL(worker).host}" != worker:
        raise UpstreamChangedError(operation, "worker_url")
    url = httpx2.URL(f"{worker}{path}")
    if not path.startswith("/") or path.startswith("//") or f"{url.scheme}://{url.host}" != worker:
        raise ValueError("A request path is absolute and on the worker")
    return url


def _bounded(within: float | None) -> httpx2.Timeout:
    """Return the timeout of a request that has `within` seconds left: each phase is cut to them."""
    if within is None:
        return _TIMEOUT
    left = max(within, 0.001)
    return httpx2.Timeout(
        connect=min(_PHASE, left), read=min(_READ, left), write=min(_PHASE, left), pool=min(_PHASE, left)
    )


def _unavailable(operation: str, failure: str) -> UpstreamUnavailableError:
    return UpstreamUnavailableError(f"Odoo.sh could not be reached ({failure}).", operation=operation)


def _unexpected(status: int, body: object) -> str | None:
    """Name the part of an answer that the upstream reference does not describe."""
    if status != httpx2.codes.OK:
        return "Location" if httpx2.codes.is_redirect(status) else "status"
    if not isinstance(body, dict):
        return "body"
    return "error.data.name" if "error" in body else None


def _error(operation: str, answer: _Answer, not_found: str | None) -> OdoucheError | None:
    status = answer.status
    if status is None:
        return _unavailable(operation, str(answer.failure))
    if httpx2.codes.is_server_error(status) or status == httpx2.codes.TOO_MANY_REQUESTS:
        return UpstreamUnavailableError(f"Odoo.sh answered with status {status}.", operation=operation, status=status)
    if status in _REFUSED:
        cls, message = _REFUSED[status]
        return cls(message, operation=operation, status=status)
    if status == httpx2.codes.OK and _error_name(answer.body) == _ACCESS_DENIED:
        return PermissionDeniedError(_DENIED, operation=operation, status=status)
    if not_found is not None and status == httpx2.codes.OK and _error_name(answer.body) == _UNEXPLAINED:
        return NotFoundError(not_found, operation=operation, status=status)
    field = _unexpected(status, answer.body)
    return UpstreamChangedError(operation, field, status=status) if field else None


class Transport:
    """Sends requests to Odoo.sh with the session, one at a time and to `HOST` only.

    A worker is asked with the project's token and never gets the session. A redirect is never
    followed. The bus socket is opened here too, so it goes to `HOST` like a request.
    `on_rejected` is called when Odoo.sh rejects the session, before `SessionExpiredError` is
    raised.
    """

    def __init__(
        self,
        session: Secret,
        *,
        on_rejected: Callable[[], None] = lambda: None,
        read_only: bool = False,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._read_only = read_only
        self._on_rejected = on_rejected
        self._sleep = sleep
        self._lock = threading.Lock()
        self._client = httpx2.Client(
            headers={"User-Agent": f"odouche/{version('odouche')}"},
            timeout=_TIMEOUT,
            follow_redirects=False,
            transport=transport,
        )
        # Answers set `session_id` again: keeping it would hold the session as a bare string.
        self._client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the connections."""
        self._client.close()

    @property
    def read_only(self) -> bool:
        """Whether a state-changing request is refused."""
        return self._read_only

    def call(
        self,
        operation: str,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        retry: bool = False,
        not_found: str | None = None,
        within: float | None = None,
    ) -> dict[str, object]:
        """Send one JSON-RPC request and return the answer, which holds no `error`.

        `retry` repeats a request that failed in transit or on a gateway error. A state-changing
        request goes through `change`. `within` is the seconds a caller has left: no phase of the
        request waits longer, and it is not sent again past them.

        `not_found` is the message of the `NotFoundError` raised for the error Odoo.sh gives no
        reason for, which is its answer to a branch that does not exist. Without it that answer
        is an `UpstreamChangedError`.
        """
        answer = self._attempt(operation, "POST", _url(path), params, retry=retry, within=within)
        error = _error(operation, answer, not_found)
        if error is not None:
            raise error
        return cast("dict[str, object]", answer.body)

    def change(
        self,
        operation: str,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        refused: OdoucheError | None = None,
    ) -> dict[str, object]:
        """Send one state-changing JSON-RPC request, once: sent twice, it is done twice.

        Returns the answer, which holds no `error`. Raises `ReadOnlyError` before sending when the
        transport is read-only, and `OutcomeUnknownError` when the request left and nothing says
        what Odoo.sh did with it: no answer, a server error, or the error Odoo.sh gives no reason for.
        `refused` is raised when Odoo.sh answers that a value is not valid, which changes nothing.
        Without it that answer is an `UpstreamChangedError`.
        """
        if self._read_only:
            raise _read_only(operation)
        answer = self._attempt(operation, "POST", _url(path), params, retry=False)
        if answer.status is None:
            unknown = answer.failure not in _NOT_SENT
        else:
            unknown = httpx2.codes.is_server_error(answer.status) or _error_name(answer.body) == _UNEXPLAINED
        if unknown:
            raise OutcomeUnknownError(
                f"Odoo.sh did not confirm the {operation}, which may have been done. Look before trying again.",
                operation=operation,
                status=answer.status,
            )
        if refused is not None and answer.status == httpx2.codes.OK and _error_name(answer.body) in _INVALID:
            raise refused
        error = _error(operation, answer, None)
        if error is not None:
            raise error
        return cast("dict[str, object]", answer.body)

    def leave(
        self, operation: str, path: str, params: Mapping[str, str] | None = None, *, to: str, retry: bool = False
    ) -> None:
        """Send one `GET` that Odoo.sh answers by sending the browser to the path `to`, and follow nothing.

        `params` is the query. Any answer other than a 303 to `to` raises as in `call`.
        """
        answer = self._attempt(operation, "GET", _url(path), params, retry=retry)
        if answer.status == httpx2.codes.SEE_OTHER and _lands_on(answer.location, to):
            return
        if answer.status == httpx2.codes.OK:
            raise UpstreamChangedError(operation, "status", status=answer.status)
        raise cast("OdoucheError", _error(operation, answer, None))

    def worker_call(self, operation: str, worker: str, path: str, token: Secret) -> dict[str, object]:
        """Send one JSON-RPC request to a worker, with `token` and without the session.

        It is repeated as a `call` with `retry` is, so it is for reads only.
        """
        url = _worker_url(operation, worker, path)
        answer = self._attempt(operation, "POST", url, {"token": token.expose_secret()}, retry=True)
        error = _error(operation, answer, None)
        if error is not None:
            raise error
        return cast("dict[str, object]", answer.body)

    @contextmanager
    def worker_read(
        self, operation: str, worker: str, path: str, token: Secret, byte_range: str
    ) -> Generator[Part | None]:
        """Open one `GET` of a range of a worker's file, with `token` and without the session.

        Gives the part answered, or `None` for an empty file. It is not repeated, and other
        requests are not held back while it is open.
        """
        url = _worker_url(operation, worker, path)
        request = self._client.build_request(
            "GET",
            url,
            params={"token": token.expose_secret()},
            # A compressed answer would not count in the file's bytes.
            headers={"Range": byte_range, "Accept-Encoding": "identity"},
        )
        response = failure = None
        try:
            response = self._client.send(request, stream=True)
        except httpx2.RequestError as error:
            failure = type(error).__name__
        _logger.debug("GET %s: %s", url.path, failure or cast("httpx2.Response", response).status_code)
        if response is None:
            raise _unavailable(operation, str(failure))
        try:
            yield _part(operation, response)
        finally:
            response.close()

    @contextmanager
    def bus(self, operation: str, within: float | None = None) -> Generator[Socket]:
        """Open the bus socket with the session. It is not repeated, and holds no other request back.

        `within` bounds the opening as it bounds a `call`.
        """
        url = _url(_BUS_PATH)
        session = status = failure = None
        with ExitStack() as stack:
            try:
                session = stack.enter_context(
                    self._client.websocket(
                        url,
                        params={"version": _BUS_VERSION},
                        headers={"Origin": f"https://{HOST}", "Cookie": f"session_id={self._session.expose_secret()}"},
                        max_message_size_bytes=_BUS_MESSAGE,
                        # No bound: the thread that reads waits on a full queue, and closing waits on it.
                        queue_size=0,
                        # The page keeps the socket open with a frame of its own: see `Socket.idle`.
                        keepalive_ping_interval_seconds=None,
                        timeout=_bounded(within),
                    )
                )
            except WebSocketUpgradeError as error:
                status = error.response.status_code
            except httpx2.RequestError as error:
                failure = type(error).__name__
            _logger.debug("GET %s: %s", url.path, failure or status or httpx2.codes.SWITCHING_PROTOCOLS.value)
            if session is None:
                raise _not_opened(operation, status, failure)
            stack.callback(_hang_up, session)
            yield Socket(operation, session)

    def _attempt(
        self,
        operation: str,
        method: str,
        url: httpx2.URL,
        params: Mapping[str, object] | None,
        *,
        retry: bool,
        within: float | None = None,
    ) -> _Answer:
        """Send a request, again if it may be, and raise if Odoo.sh rejects the session."""
        end = None if within is None else time.monotonic() + within
        with self._lock:
            answer = self._send(method, url, params, within)
            for delay in _BACKOFF if retry else ():
                left = None if end is None else end - time.monotonic() - delay
                if not answer.transient or (left is not None and left <= 0):
                    break
                self._sleep(delay)
                answer = self._send(method, url, params, left)
        rejected = answer.status is not None and is_unauthenticated(answer.status, answer.location, answer.body)
        # A worker knows nothing of the session.
        if rejected and url.host == HOST:
            self._on_rejected()
            raise SessionExpiredError(
                "Odoo.sh rejected the session. Log in again.", operation=operation, status=answer.status
            )
        return answer

    def _send(
        self, method: str, url: httpx2.URL, params: Mapping[str, object] | None, within: float | None = None
    ) -> _Answer:
        """Make one attempt: a `POST` with `params` as a JSON-RPC body, or a `GET` with them as the query.

        A failure is returned, not raised: the client's exception holds the request, and an error
        raised while it is being handled would carry it as its context.
        """
        headers = {"Cookie": f"session_id={self._session.expose_secret()}"} if url.host == HOST else {}
        started = time.perf_counter()
        try:
            if method == "GET":
                query = {key: str(value) for key, value in (params or {}).items()}
                response = self._client.get(url, params=query or None, headers=headers, timeout=_bounded(within))
            else:
                response = self._client.post(
                    url,
                    json={"jsonrpc": "2.0", "method": "call", "params": dict(params or {}), "id": 1},
                    headers=headers,
                    timeout=_bounded(within),
                )
        except httpx2.RequestError as error:
            answer = _Answer(failure=type(error).__name__)
        else:
            answer = _Answer(response.status_code, response.headers.get("Location"), _json(response.content))
        elapsed = (time.perf_counter() - started) * 1000
        _logger.debug("%s %s: %s in %.0f ms", method, url.path, answer.status or answer.failure, elapsed)
        return answer


def state_changing[**P, R](
    function: Callable[Concatenate[Transport, P], R],
) -> Callable[Concatenate[Transport, P], R]:
    """Mark an endpoint function as one that changes state on Odoo.sh.

    With a read-only transport it raises `ReadOnlyError` before anything is sent, its reads included.
    """

    @wraps(function)
    def guarded(transport: Transport, *args: P.args, **kwargs: P.kwargs) -> R:
        if transport.read_only:
            raise _read_only(function.__name__)
        return function(transport, *args, **kwargs)

    return guarded


def _read_only(operation: str) -> ReadOnlyError:
    return ReadOnlyError(f"This client is read-only: it does not send a {operation}.", operation=operation)


def _hang_up(session: WebSocketSession) -> None:
    """End the read a socket is left in: closing does not, and Odoo.sh may never answer."""
    with suppress(WebSocketNetworkError, LocalProtocolError):
        session.send(CloseConnection(1000))
    connection = session.stream.get_extra_info("socket")
    if isinstance(connection, socket.socket):
        with suppress(OSError):
            connection.shutdown(socket.SHUT_RDWR)


def _not_opened(operation: str, status: int | None, failure: str | None) -> OdoucheError:
    if status == httpx2.codes.OK:
        return UpstreamChangedError(operation, "status", status=status)
    return cast("OdoucheError", _error(operation, _Answer(status, failure=failure), None))


def _part(operation: str, response: httpx2.Response) -> Part | None:
    status = response.status_code
    chunks = _chunks(operation, response)
    if status == httpx2.codes.PARTIAL_CONTENT:
        found = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if found is None:
            raise UpstreamChangedError(operation, "Content-Range", status=status)
        return Part(int(found.group(1)), chunks)
    if status == httpx2.codes.OK:
        # An empty file. A body is a range that was not honoured.
        if any(chunks):
            raise UpstreamChangedError(operation, "body", status=status)
        return None
    raise cast("OdoucheError", _error(operation, _Answer(status, response.headers.get("Location")), None))


def _chunks(operation: str, response: httpx2.Response) -> Iterator[bytes]:
    """Yield a body as it arrives. The failure is raised out of the handler, as in `_send`."""
    chunks = response.iter_bytes()
    while True:
        try:
            chunk = next(chunks, None)
        except httpx2.RequestError as error:
            failure = type(error).__name__
            break
        if chunk is None:
            return
        yield chunk
    raise _unavailable(operation, failure)


def _json(content: bytes) -> object:
    try:
        return json.loads(content)
    except ValueError:
        return None
