import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from odouche import LoginError, Secret
from odouche._upstream import browser
from odouche._upstream.browser import Capture, find
from odouche._upstream.transport import HOST


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the browser is not launched on Windows")

SESSION = "s3ss10n-v4lu3"
OTHER = "0th3r-v4lu3"
FAKE = Path(__file__).parent / "fake_browser.py"
SOURCE = Path(browser.__file__)
ENDING = (signal.SIGTERM, signal.SIGHUP)
METHODS = ["Target.getTargets", "Target.attachToTarget", "Network.getCookies", "Browser.close"]


def cookie(value=SESSION, name="session_id", domain=HOST):
    return {"name": name, "value": value, "domain": domain, "path": "/", "httpOnly": True}


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def signalled(capture, signum, times=1):
    for _ in range(times):
        os.kill(os.getpid(), signum)
    return capture.cookie()


def spawning_with(monkeypatch, signum):
    """Send the signal to the program as soon as the browser is launched."""
    spawn = os.posix_spawn

    def spawned(*args, **kwargs):
        pid = spawn(*args, **kwargs)
        os.kill(os.getpid(), signum)
        return pid

    monkeypatch.setattr(os, "posix_spawn", spawned)


class Launched:
    """A fake browser to launch, the directory profiles are made in and the record it leaves."""

    def __init__(self, tmp_path, monkeypatch):
        self.path = str(tmp_path / "chromium")
        self.profiles = tmp_path / "profiles"
        self.log = tmp_path / "log"
        self._monkeypatch = monkeypatch
        Path(self.path).write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        Path(self.path).chmod(0o755)
        self.profiles.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(self.profiles))
        monkeypatch.setenv("FAKE_BROWSER_LOG", str(self.log))

    def serves(self, *answers, mode=""):
        self._monkeypatch.setenv("FAKE_BROWSER_COOKIES", json.dumps(answers or [[]]))
        self._monkeypatch.setenv("FAKE_BROWSER_MODE", mode)
        return self.path

    @property
    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    @property
    def sent(self):
        return [record for record in self.records if "method" in record]

    @property
    def running(self):
        return alive(self.records[0]["pid"])


@pytest.fixture
def launched(tmp_path, monkeypatch):
    launched = Launched(tmp_path, monkeypatch)
    descriptors = len(os.listdir("/dev/fd"))
    handlers = [signal.getsignal(signum) for signum in ENDING]
    yield launched
    assert list(launched.profiles.iterdir()) == []
    assert len(os.listdir("/dev/fd")) == descriptors
    assert not launched.log.exists() or not launched.running
    assert [signal.getsignal(signum) for signum in ENDING] == handlers


@pytest.fixture
def host():
    """Handlers of the host program for the signals that end it, and what they were called with."""
    received = []
    previous = {signum: signal.getsignal(signum) for signum in ENDING}
    for signum in previous:
        signal.signal(signum, lambda signum, _: received.append(signum))
    yield received
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def test_reads_the_session_of_the_host_and_nothing_else(launched):
    others = [cookie(OTHER, domain=".odoo.sh"), cookie(OTHER, name="tz"), cookie(OTHER, domain="github.com")]

    with Capture(launched.serves([], [*others, cookie()])) as capture:
        assert capture.cookie() is None
        assert capture.cookie() == Secret(SESSION)


def test_sends_four_methods_and_nothing_else(launched):
    with Capture(launched.serves([cookie()])) as capture:
        capture.cookie()
        capture.cookie()

    sent = launched.sent
    assert [message["method"] for message in sent] == [*METHODS[:3], METHODS[2], METHODS[3]]
    assert sent[1]["params"] == {"targetId": "page-1", "flatten": True}
    assert all(message["params"] == {"urls": [f"https://{HOST}"]} for message in sent[2:4])
    assert all(message["sessionId"] == "page-session-1" for message in sent[2:4])
    assert all("sessionId" not in message for message in (sent[0], sent[1], sent[4]))


def test_the_source_names_four_methods_and_asks_for_the_targets_once():
    source = SOURCE.read_text(encoding="utf-8")

    assert sorted(re.findall(r'"([A-Z][A-Za-z]+\.[a-z][A-Za-z]+)"', source)) == sorted(METHODS)


def test_launches_on_the_login_with_a_profile_of_its_own(launched):
    with Capture(launched.serves()):
        (profile,) = launched.profiles.iterdir()
        assert (profile / "Cookies").exists()
        assert profile.stat().st_mode & 0o077 == 0

    launch = launched.records[0]
    assert launch["argv"] == [
        "--remote-debugging-pipe",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--disable-sync",
        f"https://{HOST}/web/login",
    ]
    assert launch["stdio"] == [os.stat(os.devnull).st_rdev] * 3


@pytest.mark.parametrize("interruption", [RuntimeError, KeyboardInterrupt])
def test_deletes_the_profile_when_the_login_is_interrupted(launched, interruption):
    with pytest.raises(interruption), Capture(launched.serves()):
        raise interruption

    assert launched.sent[-1]["method"] == "Browser.close"


def test_a_browser_closed_by_the_user_ends_the_login(launched):
    with Capture(launched.serves(mode="closed")) as capture, pytest.raises(LoginError, match="closed"):
        capture.cookie()


def test_a_browser_with_no_window_ends_the_login(launched):
    with pytest.raises(LoginError, match="window"), Capture(launched.serves(mode="no-page")):
        pass

    assert [message["method"] for message in launched.sent] == [METHODS[0], METHODS[3]]


def test_a_browser_that_does_not_close_is_killed(launched, monkeypatch):
    monkeypatch.setattr(browser, "_EXIT_TIMEOUT", 0.2)

    with Capture(launched.serves(mode="hangs")):
        assert launched.running

    assert not launched.running


def test_the_helpers_of_the_browser_are_killed_with_it(launched):
    beat = launched.log.with_suffix(".helper")

    with Capture(launched.serves(mode="helper")):
        deadline = time.monotonic() + 5
        while not beat.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert beat.exists()

    last = beat.read_text()
    time.sleep(0.3)
    assert beat.read_text() == last


def test_an_interrupt_while_closing_still_deletes_the_profile(launched, monkeypatch):
    monkeypatch.setattr(browser, "_EXIT_TIMEOUT", 30)
    interrupt = threading.Timer(0.3, signal.pthread_kill, [threading.main_thread().ident, signal.SIGINT])

    with pytest.raises(KeyboardInterrupt), Capture(launched.serves(mode="hangs")):
        interrupt.start()

    interrupt.join()


def test_an_interrupt_during_the_launch_still_stops_the_browser(launched, monkeypatch):
    spawning_with(monkeypatch, signal.SIGINT)

    with pytest.raises(KeyboardInterrupt), Capture(launched.serves()):
        pass

    assert launched.records


@pytest.mark.parametrize("signum", ENDING)
def test_a_signal_that_ends_the_program_reaches_it_once_the_profile_is_deleted(launched, host, signum):
    left = []
    signal.signal(signum, lambda signum, _: left.append((signum, list(launched.profiles.iterdir()), launched.running)))

    with pytest.raises(LoginError, match="signal"), Capture(launched.serves([cookie()])) as capture:
        signalled(capture, signum)

    assert left == [(signum, [], False)]


def test_a_signal_during_the_launch_reaches_the_program_once_the_browser_is_stopped(launched, host, monkeypatch):
    spawning_with(monkeypatch, signal.SIGTERM)

    with pytest.raises(LoginError, match="signal"), Capture(launched.serves()):
        pass

    assert launched.records
    assert host == [signal.SIGTERM]


def test_a_signal_while_closing_waits_for_the_profile_to_be_deleted(launched, host, monkeypatch):
    monkeypatch.setattr(browser, "_EXIT_TIMEOUT", 1.5)
    left = []
    signal.signal(signal.SIGTERM, lambda *_: left.append((list(launched.profiles.iterdir()), launched.running)))
    terminate = threading.Timer(0.2, signal.pthread_kill, [threading.main_thread().ident, signal.SIGTERM])

    with Capture(launched.serves(mode="hangs")):
        terminate.start()

    terminate.join()
    assert left == [([], False)]


def test_a_signal_is_delivered_once_however_often_it_is_sent(launched, host):
    with pytest.raises(LoginError, match="signal"), Capture(launched.serves([cookie()])) as capture:
        signalled(capture, signal.SIGTERM, times=2)

    assert host == [signal.SIGTERM]


def test_a_signal_the_program_ignores_stays_ignored(launched, host):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    with Capture(launched.serves([cookie()])) as capture:
        assert signalled(capture, signal.SIGHUP) == Secret(SESSION)

    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN


def test_runs_outside_the_main_thread_where_no_signal_can_be_handled(launched, host):
    handlers = [signal.getsignal(signum) for signum in ENDING]
    read = []

    def capture():
        with Capture(launched.serves([cookie()])) as capture:
            read.extend([capture.cookie(), *(signal.getsignal(signum) for signum in ENDING)])

    thread = threading.Thread(target=capture)
    thread.start()
    thread.join()

    assert read == [Secret(SESSION), *handlers]


def test_a_program_that_is_terminated_dies_of_the_signal_with_the_profile_deleted(launched):
    program = (
        "import sys, time\n"
        "from odouche._upstream.browser import Capture\n"
        "with Capture(sys.argv[1]) as capture:\n"
        "    print('ready', flush=True)\n"
        "    while True:\n"
        "        capture.cookie()\n"
        "        time.sleep(0.05)\n"
    )
    environ = {**os.environ, "TMPDIR": str(launched.profiles)}
    with subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", program, launched.serves()], env=environ, stdout=subprocess.PIPE
    ) as process:
        assert process.stdout is not None
        assert process.stdout.readline() == b"ready\n"
        assert list(launched.profiles.iterdir())
        process.terminate()
        try:
            process.wait(timeout=20)
        finally:
            process.kill()

    assert process.returncode == -signal.SIGTERM


def test_a_browser_that_keeps_its_profile_elsewhere_is_refused(launched):
    with pytest.raises(LoginError, match="profile"), Capture(launched.serves(mode="elsewhere")):
        pass


def test_reads_the_pipe_whatever_its_descriptor_number(launched):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if 0 <= hard < 1200:
        pytest.skip("the limit on open files is too low to pass descriptor 1024")
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard if 0 <= soft < 1200 else soft, hard))
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(1100)]
    try:
        with Capture(launched.serves([cookie()])) as capture:
            assert capture.cookie() == Secret(SESSION)
    finally:
        for fd in held:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_a_browser_that_cannot_be_launched_leaves_nothing(launched):
    with pytest.raises(LoginError, match="FileNotFoundError"), Capture(launched.path + "-missing"):
        pass


def test_a_profile_that_cannot_be_deleted_is_named(launched, monkeypatch):
    monkeypatch.setattr(browser, "_REMOVAL_TIMEOUT", 0.2)
    rmtree = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", lambda *_, **__: None)

    with pytest.raises(LoginError) as raised, Capture(launched.serves([cookie()])) as capture:
        capture.cookie()

    (profile,) = launched.profiles.iterdir()
    assert str(profile) in str(raised.value)
    assert SESSION not in str(raised.value)
    rmtree(profile)


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"cookies": None},
        {"cookies": ["session_id"]},
        {"cookies": [cookie(name="session")]},
        {"cookies": [cookie(domain=".odoo.sh")]},
        {"cookies": [cookie(domain=f"{HOST}.example.com")]},
        {"cookies": [cookie(value="")]},
        {"cookies": [{**cookie(), "value": 42}]},
    ],
)
def test_takes_no_other_cookie_for_the_session(result):
    assert browser._session(result) is None


@pytest.mark.parametrize(
    ("platform", "environ", "installed", "found"),
    [
        ("linux", {"DISPLAY": ":0"}, ["/usr/bin/chromium"], "/usr/bin/chromium"),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, ["/usr/bin/brave-browser"], "/usr/bin/brave-browser"),
        ("linux", {"DISPLAY": ":0"}, ["/usr/bin/firefox"], None),
        ("linux", {"DISPLAY": ""}, ["/usr/bin/chromium"], None),
        ("linux", {}, ["/usr/bin/chromium"], None),
        ("darwin", {}, [browser._APPLICATIONS[2]], browser._APPLICATIONS[2]),
        ("darwin", {}, ["/Applications/Firefox.app/Contents/MacOS/firefox"], None),
        ("win32", {"DISPLAY": ":0"}, ["/usr/bin/chromium", browser._APPLICATIONS[0]], None),
    ],
)
def test_finds_a_chromium_browser_that_can_open_a_window(monkeypatch, platform, environ, installed, found):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", lambda name: next((p for p in installed if p.endswith(f"/{name}")), None))
    monkeypatch.setattr(os, "access", lambda path, _: path in installed)

    assert find(environ) == found
