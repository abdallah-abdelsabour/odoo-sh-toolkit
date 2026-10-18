"""The browser a login runs in, and the one thing read from it: the Odoo.sh session cookie.

This is the only code that speaks to the browser. It sends four methods and nothing else:
`Target.getTargets` once, `Target.attachToTarget`, `Network.getCookies` for `HOST` and
`Browser.close`. It enables no domain, subscribes to no event and injects no script.
"""

import json
import os
import selectors
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Self, cast

from odouche._upstream.transport import HOST
from odouche.errors import LoginError
from odouche.secret import Secret


_LOGIN_URL = f"https://{HOST}/web/login"
_COOKIE = "session_id"

_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
)
_APPLICATIONS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)

_REPLY_TIMEOUT = 30.0
_EXIT_TIMEOUT = 10.0
_REMOVAL_TIMEOUT = 5.0
_PAUSE = 0.1


def find(environ: Mapping[str, str] = os.environ) -> str | None:
    """Return a Chromium-family browser that can open a window here, or nothing."""
    if sys.platform == "win32":
        return None
    if sys.platform == "darwin":
        return next((path for path in _APPLICATIONS if os.access(path, os.X_OK)), None)
    if not (environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")):
        return None
    return next((path for path in map(shutil.which, _NAMES) if path), None)


def _session(result: dict[str, object]) -> Secret | None:
    """Take the session out of a `Network.getCookies` result and keep nothing else of it."""
    cookies = result.get("cookies")
    for cookie in cast("list[object]", cookies) if isinstance(cookies, list) else ():
        if not isinstance(cookie, dict):
            continue
        fields = cast("dict[str, object]", cookie)
        value = fields.get("value")
        if fields.get("name") == _COOKIE and fields.get("domain") == HOST and isinstance(value, str) and value:
            return Secret(value)
    return None


def _answer(raw: bytes, awaited: int) -> tuple[bool, dict[str, object] | None]:
    """Tell whether a message answers the awaited command, and return its result if it has one."""
    try:
        message = json.loads(raw)
    except ValueError:
        return False, None
    if not isinstance(message, dict) or cast("dict[str, object]", message).get("id") != awaited:
        return False, None
    result = cast("dict[str, object]", message).get("result")
    return True, cast("dict[str, object]", result) if isinstance(result, dict) else None


class Capture:
    """A browser open on the Odoo.sh login, in a profile that is deleted when the block ends.

    In the main thread, `SIGTERM` and `SIGHUP` are held while the block runs: the next command
    raises `LoginError`, and the signal is delivered once the profile is deleted.
    """

    def __init__(self, browser: str) -> None:
        self._browser = browser
        self._profile: str | None = None
        self._pid: int | None = None
        self._fds: list[int] = []
        self._readable = selectors.DefaultSelector()
        self._buffer = b""
        self._last = 0
        self._page: str | None = None
        self._handlers: dict[int, Any] = {}
        self._signalled: int | None = None

    def __enter__(self) -> Self:
        try:
            self._trap()
            self._launch()
            self._attach()
        except BaseException:
            self._end()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self._end()

    def cookie(self) -> Secret | None:
        """Return the `session_id` cookie of `HOST` as it is now, signed in or not."""
        result = self._ask("Network.getCookies", {"urls": [f"https://{HOST}"]}, self._page)
        if result is None:
            raise LoginError("The browser was closed before the login completed.")
        return _session(result)

    def _trap(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            handler = signal.getsignal(signum)
            # An ignored signal stays ignored, and a handler Python did not set cannot be put back.
            if handler is None or handler == signal.SIG_IGN:
                continue
            try:
                signal.signal(signum, self._on_signal)
            except ValueError:
                # Not the main thread, which is the only one a signal handler runs in.
                return
            self._handlers[signum] = handler

    def _on_signal(self, signum: int, _frame: object) -> None:
        # Only noted: raised from here, it could land where it skips the deletion of the profile.
        self._signalled = signum

    def _end(self) -> None:
        """Close, then deliver the signal that was held to whatever handled it before."""
        try:
            self._close()
        finally:
            for trapped, handler in self._handlers.items():
                signal.signal(trapped, handler)
            self._handlers = {}
            signum, self._signalled = self._signalled, None
            if signum is not None:
                signal.raise_signal(signum)

    def _launch(self) -> None:
        import fcntl  # noqa: PLC0415

        self._profile = tempfile.mkdtemp(prefix="odouche-login-")
        to_browser, ours_out = os.pipe()
        ours_in, from_browser = os.pipe()
        self._fds = [ours_in, ours_out]
        self._readable.register(ours_in, selectors.EVENT_READ)
        # Moved clear of 3 and 4, so that neither `dup2` below lands on the other's source.
        ends = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10) for fd in (to_browser, from_browser)]
        # An interrupt between the launch and the note of its pid would leave a browser nothing stops.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            self._pid = os.posix_spawn(
                self._browser,
                [
                    self._browser,
                    "--remote-debugging-pipe",
                    f"--user-data-dir={self._profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--password-store=basic",
                    "--disable-sync",
                    _LOGIN_URL,
                ],
                os.environ,
                file_actions=[
                    (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0),
                    (os.POSIX_SPAWN_DUP2, ends[0], 3),
                    (os.POSIX_SPAWN_DUP2, ends[1], 4),
                ],
                # A group of its own, so that the browser and its children can be killed together.
                setpgroup=0,
                setsigmask=mask,
            )
        except OSError as error:
            failure = type(error).__name__
        else:
            return
        finally:
            try:
                for fd in (to_browser, from_browser, *ends):
                    os.close(fd)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        raise LoginError(f"The browser could not be launched ({failure}).")

    def _attach(self) -> None:
        targets = (self._ask("Target.getTargets") or {}).get("targetInfos")
        for target in cast("list[object]", targets) if isinstance(targets, list) else ():
            fields = cast("dict[str, object]", target) if isinstance(target, dict) else {}
            if fields.get("type") != "page":
                continue
            attached = self._ask("Target.attachToTarget", {"targetId": fields.get("targetId"), "flatten": True})
            page = (attached or {}).get("sessionId")
            if isinstance(page, str):
                self._page = page
                break
        else:
            raise LoginError("The browser did not open a window to log in from.")
        # A sandboxed browser can be given a directory of its own in place of this one.
        if self._profile is None or not os.listdir(self._profile):
            raise LoginError("The browser does not keep its profile where it can be deleted after the login.")

    def _send(self, method: str, params: dict[str, object] | None = None, page: str | None = None) -> int:
        self._last += 1
        message: dict[str, object] = {"id": self._last, "method": method, "params": params or {}}
        if page is not None:
            message["sessionId"] = page
        os.write(self._fds[1], json.dumps(message).encode() + b"\0")
        return self._last

    def _ask(
        self, method: str, params: dict[str, object] | None = None, page: str | None = None
    ) -> dict[str, object] | None:
        """Send one command and return its result, or nothing if the browser does not give one."""
        if self._signalled is not None:
            raise LoginError("The login was ended by a signal.")
        try:
            awaited = self._send(method, params, page)
        except OSError:
            return None
        deadline = time.monotonic() + _REPLY_TIMEOUT
        while True:
            # Anything that is not the answer is dropped: no event is subscribed to.
            while b"\0" in self._buffer:
                raw, _, self._buffer = self._buffer.partition(b"\0")
                answered, result = _answer(raw, awaited)
                if answered:
                    return result
            if not self._read(deadline):
                return None

    def _read(self, deadline: float) -> bool:
        """Take what the browser wrote, and tell whether there was anything before the deadline."""
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._readable.select(remaining):
            return False
        try:
            chunk = os.read(self._fds[0], 65536)
        except OSError:
            return False
        self._buffer += chunk
        return bool(chunk)

    def _close(self) -> None:
        """Stop the browser, then delete the profile. Each step runs even if the one before fails."""
        pid, self._pid = self._pid, None
        profile, self._profile = self._profile, None
        try:
            if pid is not None:
                self._stop(pid)
        finally:
            try:
                self._readable.close()
                for fd in self._fds:
                    os.close(fd)
            finally:
                self._fds = []
                self._buffer = b""
                if profile is not None and not _remove(profile):
                    raise LoginError(
                        f"The browser profile at {profile} could not be deleted. Delete it by hand: "
                        "it can hold your GitHub session."
                    )

    def _stop(self, pid: int) -> None:
        try:
            with suppress(OSError):
                self._send("Browser.close")
            # The browser closes its end as it exits.
            deadline = time.monotonic() + _EXIT_TIMEOUT
            while self._read(deadline):
                self._buffer = b""
        finally:
            # Its helpers can outlive it, and write to the profile until they end. The browser is
            # not reaped before this, so its group cannot have become another process's.
            with suppress(OSError):
                os.killpg(pid, signal.SIGKILL)
            with suppress(ChildProcessError):
                os.waitpid(pid, 0)


def _remove(profile: str) -> bool:
    deadline = time.monotonic() + _REMOVAL_TIMEOUT
    while True:
        shutil.rmtree(profile, ignore_errors=True)
        if not os.path.lexists(profile):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_PAUSE)
