import dataclasses
import json
import logging
import queue
import socket
import sys
import threading
import time as real
import traceback
from pathlib import Path

import httpcore2
import httpx2
import pytest
from httpcore2._backends.sync import SyncStream
from wsproto import ConnectionType
from wsproto.connection import Connection
from wsproto.events import BytesMessage, TextMessage

from odouche import (
    BuildResult,
    BuildStatus,
    NotFoundError,
    OdoucheError,
    SessionExpiredError,
    StreamTimeoutError,
    UpstreamChangedError,
    UpstreamUnavailableError,
)
from odouche._upstream import watch
from odouche._upstream.transport import Socket


FIXTURES = Path(__file__).parent / "fixtures"
BRANCH = 51044
BUILD = 88240
PROJECT = "acme-shop"
PROJECTS = "/app/projects"
BUILDS = "/app/branch/51044/builds"
SOCKET = "/websocket"
EVENTS = json.loads((FIXTURES / "build_events.json").read_text())
# The build as its last event has it: dropped, once a newer build replaced it.
DROPPED = EVENTS[3][0]
UNAUTHENTICATED = json.loads((FIXTURES / "unauthenticated.json").read_text())
DROP = object()


class Stream:
    """One connection of the socket: sends what the bus is given, and keeps what the client writes."""

    def __init__(self, bus):
        self.bus = bus
        self.closed = threading.Event()
        self.outgoing = Connection(ConnectionType.SERVER)
        self.incoming = Connection(ConnectionType.SERVER)

    def read(self, *_):
        while not self.closed.is_set():
            try:
                frame = self.bus.frames.get(timeout=0.01)
            except queue.Empty:
                continue
            if frame is DROP:
                break
            message = BytesMessage(frame) if isinstance(frame, bytes) else TextMessage(frame)
            return self.outgoing.send(message)
        return b""

    def write(self, data, *_):
        if self.bus.refused_writes:
            self.bus.refused_writes -= 1
            raise httpcore2.WriteError("refused")
        self.incoming.receive_data(data)
        for event in self.incoming.events():
            if isinstance(event, TextMessage):
                self.bus.sent.append(json.loads(event.data))
            elif isinstance(event, BytesMessage):
                self.bus.sent.append(bytes(event.data))

    def close(self):
        self.closed.set()

    def get_extra_info(self, _):
        return None


class Bus:
    """Opens the socket as Odoo.sh does, and sends the frames it is given to whoever is connected."""

    def __init__(self):
        self.frames = queue.Queue()
        self.sent = []
        self.streams = []
        self.requests = []
        # What the next handshakes do in place of opening: a status or an error.
        self.failures = []
        self.refused_writes = 0
        # A real connection to hand over in place of a `Stream`.
        self.wire = None

    def __call__(self, request):
        self.requests.append(request)
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, type):
            raise failure(f"failed, Cookie: {request.headers['Cookie']}", request=request)
        if failure is not None:
            return httpx2.Response(failure)
        self.streams.append(self.wire or Stream(self))
        return httpx2.Response(101, extensions={"network_stream": self.streams[-1]})

    def push(self, *notifications):
        self.frames.put(json.dumps(notifications))

    def drop(self):
        self.frames.put(DROP)


class Time:
    """A clock that only a sleep or a test moves."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def time(monkeypatch):
    time = Time()
    monkeypatch.setattr(watch, "_monotonic", time.monotonic)
    monkeypatch.setattr(watch, "_sleep", time.sleep)
    receive = Socket.receive

    def bounded(socket, timeout):
        """Fail where a watch would wait for a frame no test sends."""
        started = real.monotonic()
        frame = receive(socket, min(timeout, 5))
        assert frame is not None or real.monotonic() - started < 5
        return frame

    monkeypatch.setattr(Socket, "receive", bounded)
    return time


@pytest.fixture
def bus(upstream):
    upstream.bodies[SOCKET] = Bus()
    return upstream.bodies[SOCKET]


@pytest.fixture
def build(client):
    return client.build(BRANCH, BUILD)


@pytest.fixture
def asked(upstream, build):
    """The paths asked after the build was read."""
    upstream.requests.clear()
    return lambda: [request.url.path for request in upstream.requests]


def event(notification_id, build_id=BUILD, **values):
    payload = {"repository_id": 4217, "branch_id": BRANCH, "build_id": build_id, "values": {"id": build_id, **values}}
    return {"id": notification_id, "message": {"type": "paas.repository/build_event", "payload": payload}}


def whole(notification_id, **values):
    return event(notification_id, **{**DROPPED["message"]["payload"]["values"], **values})


def answer(upstream, **values):
    """Change what the builds request says of the build."""
    upstream.bodies[BUILDS]["result"][0]["builds"][0].update(values)


def reading(thread):
    """Tell whether a thread is in a read of the socket, which nothing but the peer would end."""
    frame = sys._current_frames().get(thread.ident)
    return any(caller.name in {"read", "recv"} for caller in traceback.extract_stack(frame)) if frame else False


def state(build):
    return build.status, build.result, build.status_info


def test_yields_each_change_and_ends_after_the_finished_build(client, build, bus, time, asked):
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    bus.push(whole(2, status="done", result="success", status_info="done", run_time="0:02:17"))

    watched = list(client.watch_build(PROJECT, build, timeout=600))

    assert [state(build) for build in watched] == [
        (BuildStatus.PROGRESS, None, None),
        (BuildStatus.PROGRESS, None, "Testing: invoicing"),
        (BuildStatus.DONE, BuildResult.SUCCESS, "done"),
    ]
    assert watched[0] == build
    assert watched[1] == dataclasses.replace(build, status_info="Testing: invoicing")
    assert watched[2].finished
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS]
    assert time.sleeps == []


def test_a_build_that_has_finished_is_yielded_once_and_opens_no_socket(client, upstream, build, bus, time, asked):
    answer(upstream, status="done", result="success", status_info="done")

    (watched,) = client.watch_build(PROJECT, build, timeout=600)

    assert state(watched) == (BuildStatus.DONE, BuildResult.SUCCESS, "done")
    assert asked() == [BUILDS]


def test_opens_the_socket_as_the_page_does_and_subscribes_to_the_project(client, build, bus, time, session):
    bus.push(whole(7))

    list(client.watch_build(PROJECT, build, timeout=600))

    (request,) = bus.requests
    assert request.method == "GET"
    assert str(request.url) == "https://www.odoo.sh/websocket?version=18.0-7"
    assert request.headers["Origin"] == "https://www.odoo.sh"
    assert request.headers["Cookie"] == f"session_id={session}"
    assert request.headers["Upgrade"] == "websocket"
    assert bus.sent == [{"event_name": "subscribe", "data": {"channels": ["paas_repository:4217"], "last": 0}}]


def test_takes_a_project_without_asking_for_it(client, build, bus, time, asked):
    project = client.projects()[0]
    bus.push(whole(7))
    asked()

    list(client.watch_build(project, build, timeout=600))

    assert asked()[-3:] == [BUILDS, SOCKET, BUILDS]


def test_a_project_the_user_cannot_reach_is_not_found(client, build, bus, time):
    watching = client.watch_build("no-such-project", build, timeout=600)
    next(watching)

    with pytest.raises(NotFoundError, match="no-such-project"):
        next(watching)

    assert bus.requests == []


def test_a_build_that_is_no_longer_listed_is_not_found(client, build, bus, time):
    with pytest.raises(NotFoundError):
        list(client.watch_build(PROJECT, dataclasses.replace(build, id=1), timeout=600))


def test_stays_on_its_build_and_ends_when_a_newer_one_drops_it(client, build, bus, time):
    for frame in EVENTS:
        bus.push(*frame)

    watched = list(client.watch_build(PROJECT, build, timeout=600))

    assert [state(build) for build in watched[1:]] == [(BuildStatus.DROPPED, BuildResult.SUCCESS, "done")]
    assert {build.id for build in watched} == {BUILD}


def test_an_event_that_changes_nothing_is_not_yielded(client, build, bus, time):
    bus.push(event(1, status="progress", result=False))
    bus.push(event(2, status="progress", result=False, status_info="Testing: invoicing"))
    bus.push(whole(3, status="progress", result=False, status_info="Testing: invoicing"))
    bus.push(whole(4))

    watched = list(client.watch_build(PROJECT, build, timeout=600))

    assert [build.status_info for build in watched] == [None, "Testing: invoicing", "done"]


def test_what_happened_before_the_socket_opened_comes_from_the_builds_request(client, upstream, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    answer(upstream, status_info="Testing: invoicing")

    assert next(watching).status_info == "Testing: invoicing"
    watching.close()


def test_asks_for_the_build_after_a_quiet_spell(client, upstream, build, bus, time, asked):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    time.now += 59
    bus.push(event(2, status="progress", result=False, status_info="Testing: stock"))
    next(watching)
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS]
    answer(upstream, status="done", result="success", status_info="done")
    time.now += 60

    assert next(watching).result is BuildResult.SUCCESS
    assert list(watching) == []
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS, BUILDS]
    assert bus.sent[1:] == [b"\x00"]


def test_a_frame_that_is_not_text_is_passed_over(client, build, bus, time):
    bus.frames.put(b"\x00")
    bus.push(whole(7))

    assert len(list(client.watch_build(PROJECT, build, timeout=600))) == 2


def test_opens_the_socket_again_when_it_drops_and_names_the_last_notification(
    client, upstream, build, bus, time, asked
):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(41, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    answer(upstream, status_info="Testing: invoicing")
    bus.drop()
    bus.push(whole(42))

    assert [build.status for build in watching] == [BuildStatus.DROPPED]
    assert time.sleeps == [1.0]
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS, SOCKET, BUILDS]
    assert [sent["data"]["last"] for sent in bus.sent] == [0, 41]
    assert all(stream.closed.is_set() for stream in bus.streams)


def test_opens_the_socket_again_when_it_cannot_be_written_to(client, upstream, build, bus, time):
    bus.refused_writes = 1
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    answer(upstream, status="done", result="success", status_info="done")

    assert next(watching).finished
    assert time.sleeps == [1.0]
    assert len(bus.streams) == 2


def test_opens_the_socket_again_when_it_closed_while_nothing_was_read(client, upstream, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    bus.drop()
    assert bus.streams[0].closed.wait(5)
    answer(upstream, status="done", result="success", status_info="done")
    time.now += 60

    assert next(watching).finished
    assert time.sleeps == [1.0]
    assert len(bus.streams) == 2


def test_a_builds_request_that_fails_ends_the_watch_and_is_not_sent_again(client, upstream, build, bus, time, asked):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    upstream.failures[BUILDS] = 502
    time.now += 60

    with pytest.raises(UpstreamUnavailableError) as raised:
        next(watching)

    assert raised.value.operation == "builds"
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS, BUILDS, BUILDS, BUILDS]
    assert time.sleeps == []
    assert bus.streams[0].closed.is_set()


@pytest.mark.parametrize("failure", [httpx2.ConnectError, 502])
def test_gives_up_after_the_third_failure_in_a_row(client, build, bus, time, failure):
    bus.failures = [failure] * 3
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)

    with pytest.raises(UpstreamUnavailableError) as raised:
        next(watching)

    assert raised.value.operation == "watch"
    assert time.sleeps == [1.0, 2.0]
    assert len(bus.requests) == 3


def test_a_socket_that_brought_something_starts_the_count_of_failures_again(client, upstream, build, bus, time):
    bus.failures = [httpx2.ConnectError] * 2
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    watching = client.watch_build(PROJECT, build, timeout=600)
    assert [build.status_info for build in (next(watching), next(watching))] == [None, "Testing: invoicing"]
    answer(upstream, status_info="Testing: invoicing")
    bus.drop()
    bus.failures = [httpx2.ConnectError]
    bus.push(whole(2))

    assert [build.status for build in watching] == [BuildStatus.DROPPED]
    assert time.sleeps == [1.0, 2.0, 1.0, 2.0]


def test_a_build_still_watched_at_the_timeout_raises_the_timeout_error(client, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=90)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    time.now += 90

    with pytest.raises(StreamTimeoutError) as raised:
        next(watching)

    assert not isinstance(raised.value, UpstreamUnavailableError)
    assert raised.value.operation == "watch"
    assert raised.value.__context__ is None
    assert bus.streams[0].closed.is_set()


def test_each_request_of_a_watch_has_what_is_left_of_the_timeout(client, upstream, build, bus, time):
    upstream.requests.clear()
    watching = client.watch_build(PROJECT, build, timeout=20)
    next(watching)
    time.now += 15
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)

    assert [request.url.path for request in upstream.requests] == [BUILDS, PROJECTS, SOCKET, BUILDS]
    assert [request.extensions["timeout"]["read"] for request in upstream.requests] == [20, 5, 5, 5]
    watching.close()


def test_a_request_the_timeout_cut_short_raises_the_timeout_error(client, upstream, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=90)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)

    def late(request):
        time.now += request.extensions["timeout"]["read"]
        raise httpx2.ReadTimeout("late", request=request)

    upstream.bodies[BUILDS] = late
    time.now += 60

    with pytest.raises(StreamTimeoutError) as raised:
        next(watching)

    assert raised.value.operation == "watch"
    assert raised.value.__context__ is None
    assert bus.streams[0].closed.is_set()


def test_the_timeout_ends_the_wait_before_the_socket_is_opened_again(client, build, bus, time):
    bus.failures = [httpx2.ConnectError] * 3
    watching = client.watch_build(PROJECT, build, timeout=2.5)
    next(watching)

    with pytest.raises(StreamTimeoutError) as raised:
        next(watching)

    assert time.sleeps == [1.0, 1.5]
    assert raised.value.__context__ is None


def test_a_pulse_yields_the_build_unchanged_while_odoo_sh_says_nothing(client, build, bus, time, asked):
    watching = client.watch_build(PROJECT, build, timeout=600, pulse=0.01)
    first = next(watching)

    assert [next(watching), next(watching)] == [first, first]
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    assert next(change for change in watching if change != first).status_info == "Testing: invoicing"
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS]
    watching.close()
    assert bus.streams[0].closed.is_set()


def test_a_pulse_is_not_put_off_by_what_happens_to_another_build(client, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=600, pulse=4)
    first = next(watching)
    bus.push(event(1, build_id=BUILD + 1, status="progress", result=False, status_info="Testing: stock"))

    assert next(watching) == first
    watching.close()


def test_a_pulse_longer_than_what_is_left_does_not_put_off_the_timeout(client, build, bus, time, monkeypatch):
    waits = []
    receive = Socket.receive
    monkeypatch.setattr(Socket, "receive", lambda socket, timeout: waits.append(timeout) or receive(socket, 0.01))
    watching = client.watch_build(PROJECT, build, timeout=3, pulse=600)
    first = next(watching)

    assert next(watching) == first
    assert waits == [3]
    watching.close()


def test_a_pulse_does_not_put_off_the_request_of_a_quiet_spell(client, upstream, build, bus, time, asked):
    watching = client.watch_build(PROJECT, build, timeout=600, pulse=0.01)
    first = next(watching)
    assert next(watching) == first
    answer(upstream, status="done", result="success", status_info="done")
    time.now += 60

    assert next(watching).result is BuildResult.SUCCESS
    assert asked() == [BUILDS, PROJECTS, SOCKET, BUILDS, BUILDS]


def test_a_pulse_does_not_put_off_the_timeout(client, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=90, pulse=0.01)
    next(watching)
    next(watching)
    time.now += 90

    with pytest.raises(StreamTimeoutError):
        next(watching)


@pytest.mark.parametrize("pulse", [0, -1])
def test_a_pulse_that_is_not_one_is_refused_before_any_request(client, build, bus, asked, pulse):
    with pytest.raises(ValueError, match="pulse"):
        client.watch_build(PROJECT, build, timeout=600, pulse=pulse)

    assert asked() == []


@pytest.mark.parametrize("timeout", [0, -1])
def test_a_timeout_that_is_not_one_is_refused_before_any_request(client, build, bus, asked, timeout):
    with pytest.raises(ValueError, match="timeout"):
        client.watch_build(PROJECT, build, timeout=timeout)

    assert asked() == []


def test_a_session_odoo_sh_rejects_ends_the_watch(client, upstream, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    upstream.bodies[BUILDS] = UNAUTHENTICATED
    time.now += 60

    with pytest.raises(SessionExpiredError):
        next(watching)

    assert bus.streams[0].closed.is_set()


def test_closing_early_closes_the_socket_and_leaves_no_thread(client, build, bus, time, asked):
    before = set(threading.enumerate())
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)
    assert not bus.streams[0].closed.is_set()
    requests = asked()

    watching.close()

    assert bus.streams[0].closed.is_set()
    assert asked() == requests
    left = set(threading.enumerate()) - before
    for thread in left:
        thread.join(1)
    assert not [thread.name for thread in left if thread.is_alive() and reading(thread)]
    assert client.build(BRANCH, BUILD) == build


def test_closing_leaves_no_thread_when_odoo_sh_stays_silent(client, upstream, build, bus, time):
    ours, theirs = socket.socketpair()
    bus.wire = SyncStream(ours)
    before = set(threading.enumerate())
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    answer(upstream, status_info="Testing: invoicing")
    next(watching)

    watching.close()

    left = set(threading.enumerate()) - before
    for thread in left:
        thread.join(1)
    # A worker of the socket's pool can be left with nothing to do: it ends with the pool.
    stuck = [thread.name for thread in left if thread.is_alive() and reading(thread)]
    theirs.close()
    assert stuck == []


def test_closing_does_not_wait_on_frames_nobody_read(client, upstream, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    answer(upstream, status_info="Testing: invoicing")
    next(watching)
    for number in range(600):
        bus.push(event(number, build_id=88241, status="progress", result=False))
    patience = real.monotonic() + 5
    while not bus.frames.empty() and real.monotonic() < patience:
        real.sleep(0.01)
    assert bus.frames.empty()

    watching.close()

    assert bus.streams[0].closed.is_set()


@pytest.mark.parametrize(
    ("frame", "field"),
    [
        ("not json", "frame"),
        ('{"id": 1}', "frame"),
        ("[1]", "frame[0]"),
        ('[{"message": {}}]', "frame[0].id"),
        ('[{"id": 1, "message": {"payload": {}}}]', "frame[0].message.type"),
        (json.dumps([event(1, status=4)]), "frame[0].message.payload.values.status"),
        (json.dumps([whole(1, head_commit_url="nowhere")]), "frame[0].message.payload.values.head_commit_url"),
    ],
)
def test_a_frame_the_reference_does_not_describe_is_a_changed_upstream(client, build, bus, time, frame, field):
    bus.frames.put(frame)

    with pytest.raises(UpstreamChangedError) as raised:
        list(client.watch_build(PROJECT, build, timeout=600))

    assert raised.value.operation == "watch"
    assert raised.value.field == field
    assert bus.streams[0].closed.is_set()


@pytest.mark.parametrize("status", [400, 200])
def test_a_socket_odoo_sh_does_not_open_is_a_changed_upstream(client, build, bus, time, status):
    bus.failures = [status]

    with pytest.raises(UpstreamChangedError) as raised:
        list(client.watch_build(PROJECT, build, timeout=600))

    assert raised.value.field == "status"
    assert raised.value.status == status


@pytest.mark.parametrize("failure", [httpx2.ConnectError, 502, 400, DROP, "not json"])
def test_an_error_and_the_library_log_hold_neither_the_session_nor_a_frame(
    client, build, bus, time, caplog, session, failure
):
    if failure is DROP or isinstance(failure, str):
        for _ in range(3):
            bus.frames.put(failure)
    else:
        bus.failures = [failure] * 3

    with caplog.at_level(logging.DEBUG), pytest.raises(OdoucheError) as raised:
        list(client.watch_build(PROJECT, build, timeout=600))

    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    shown = [str(raised.value), repr(raised.value), "".join(traceback.format_exception(raised.value))]
    shown += [f"{record.getMessage()} {record.args}" for record in caplog.records]
    assert f"GET {SOCKET}: " in "".join(shown)
    for text in shown:
        assert session not in text
        assert "not json" not in text


def test_another_request_can_be_sent_while_a_build_is_watched(client, build, bus, time):
    watching = client.watch_build(PROJECT, build, timeout=600)
    next(watching)
    bus.push(event(1, status="progress", result=False, status_info="Testing: invoicing"))
    next(watching)

    assert client.build(BRANCH, BUILD) == build
    watching.close()
