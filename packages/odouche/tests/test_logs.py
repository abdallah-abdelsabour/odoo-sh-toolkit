import dataclasses
import logging
import math
import traceback
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from odouche import (
    Log,
    LogKind,
    LogLine,
    NotFoundError,
    OdoucheError,
    PermissionDeniedError,
    Secret,
    StreamTimeoutError,
    UpstreamChangedError,
    UpstreamUnavailableError,
)
from odouche._upstream import logs


FIXTURES = Path(__file__).parent / "fixtures"
BRANCH = 51044
BUILD = 88212
PROJECT = "acme-shop"
WORKER = "eupd00.odoo.com"
TOKEN = "t0k3n-0f-th3-pr0j3ct"
BUILDS = "/app/branch/51044/builds"
INFO = "/app/project/acme-shop/get_info"
LIST = "/paas/build/88212/logs/list"
INSTALL = "/paas/build/88212/logs/install"
CONTENT = (FIXTURES / "build_log_install.txt").read_bytes()
LINES = CONTENT.decode().splitlines()


class Dropped(httpx2.SyncByteStream):
    """A body whose connection drops once `sent` has arrived."""

    def __init__(self, sent):
        self.sent = sent

    def __iter__(self):
        yield self.sent
        raise httpx2.ReadError(f"dropped, token={TOKEN}")


class File:
    """Answers the ranges of a log as a worker does, and keeps what was asked."""

    def __init__(self, content=CONTENT, chunk=None):
        self.content = content
        self.chunk = chunk
        # Whether the end of the log is answered from its first byte, however little was asked for.
        self.whole = False
        self.ranges = []
        # What the next requests do in place of answering: a status, an error, or a count of bytes to drop after.
        self.failures = []

    def __call__(self, request):
        asked = request.headers["Range"]
        self.ranges.append(asked)
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, type):
            raise failure(f"failed, token={TOKEN}", request=request)
        if failure is not None and failure >= httpx2.codes.OK:
            return httpx2.Response(failure)
        size = len(self.content)
        if not size:
            return httpx2.Response(200)
        first = max(size - int(asked[7:]), 0) if asked.startswith("bytes=-") else int(asked[6:-1])
        if self.whole and asked.startswith("bytes=-"):
            first = 0
        headers = {"Content-Range": f"bytes {first}-{size - 1}/{size}", "Set-Cookie": "session_id=w0rk3r; Path=/"}
        body = self.content[first:]
        if failure is not None:
            return httpx2.Response(206, headers=headers, stream=Dropped(body[:failure]))
        if self.chunk:
            pieces = [body[at : at + self.chunk] for at in range(0, len(body), self.chunk)]
            return httpx2.Response(206, headers=headers, content=iter(pieces))
        return httpx2.Response(206, headers=headers, content=body)


class Time:
    """A clock that only a sleep moves. `during` runs before each sleep ends."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.during = lambda: None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.during()


@pytest.fixture
def time(monkeypatch):
    time = Time()
    monkeypatch.setattr(logs, "_monotonic", time.monotonic)
    monkeypatch.setattr(logs, "_sleep", time.sleep)
    return time


@pytest.fixture
def file(upstream):
    upstream.bodies[INFO]["result"]["access_token"] = TOKEN
    upstream.bodies[INSTALL] = File()
    return upstream.bodies[INSTALL]


@pytest.fixture
def build(client):
    return client.build(BRANCH, BUILD)


@pytest.fixture
def worker_requests(upstream, build):
    """The requests sent after the build was read."""
    upstream.requests.clear()
    return upstream.requests


def texts(lines):
    return [line.text for line in lines]


def test_lists_the_logs_a_build_has(client, build, file, worker_requests):
    listed = client.logs(PROJECT, build)

    assert [(log.kind, log.name, log.size) for log in listed] == [
        (LogKind.ODOO, "odoo", "0 bytes"),
        (LogKind.PIP, "pip", "2 MB"),
        (LogKind.INSTALL, "install", "156 KB"),
    ]
    assert listed[2] == Log(LogKind.INSTALL, "install", datetime(2026, 10, 1, 10, 2, 5, tzinfo=UTC), "156 KB")
    assert [(request.url.host, request.url.path) for request in worker_requests] == [
        ("www.odoo.sh", BUILDS),
        ("www.odoo.sh", INFO),
        (WORKER, LIST),
    ]


def test_a_log_the_library_does_not_know_is_unknown_and_readable_by_its_name(client, upstream, build, file):
    upstream.bodies[LIST]["result"][2]["name"] = "cron"
    upstream.bodies["/paas/build/88212/logs/cron"] = file

    assert [(log.kind, log.name) for log in client.logs(PROJECT, build)][2] == (LogKind.UNKNOWN, "cron")
    assert texts(client.read_log(PROJECT, build, "cron")) == LINES


def test_takes_a_project_or_its_name(client, build, file):
    project = client.projects()[0]

    assert client.logs(project, build) == client.logs(project.name, build)


def test_a_log_is_immutable(client, build, file):
    line = next(client.read_log(PROJECT, build, LogKind.INSTALL))

    with pytest.raises(dataclasses.FrozenInstanceError):
        line.text = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        client.logs(PROJECT, build)[0].name = "other"


def test_a_worker_gets_the_token_and_never_the_session(client, build, file, worker_requests, session):
    list(client.read_log(PROJECT, build, LogKind.INSTALL))

    listing, read = (request for request in worker_requests if request.url.host == WORKER)
    assert listing.method == "POST"
    assert b'"params":{"token":"' + TOKEN.encode() + b'"}' in listing.content.replace(b" ", b"")
    assert (read.method, str(read.url)) == ("GET", f"https://{WORKER}{INSTALL}?token={TOKEN}")
    assert read.headers["Accept-Encoding"] == "identity"
    for request in (listing, read):
        assert "Cookie" not in request.headers
        assert session not in str(request.url)
    assert len(client._transport._client.cookies.jar) == 0


def test_reads_a_whole_log_with_the_offset_of_each_line(client, build, file):
    lines = list(client.read_log(PROJECT, build, LogKind.INSTALL))

    assert texts(lines) == LINES
    assert lines[0] == LogLine(text=LINES[0], offset=len(LINES[0]) + 1, truncated=False)
    assert lines[-1].offset == len(CONTENT)
    assert file.ranges == ["bytes=0-"]


def test_reads_a_tail(client, build, file):
    lines = list(client.read_log(PROJECT, build, LogKind.INSTALL, tail=3))

    assert texts(lines) == LINES[-3:]
    assert lines[-1].offset == len(CONTENT)
    assert file.ranges == ["bytes=-1048576"]


def test_a_tail_longer_than_the_log_is_the_whole_log(client, build, file):
    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL, tail=50)) == LINES


def test_a_tail_leaves_out_the_line_the_last_mebibyte_starts_inside(client, build, file, monkeypatch):
    monkeypatch.setattr(logs, "TAIL_BYTES", len(CONTENT) - 10)

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL, tail=50)) == LINES[1:]


def test_a_part_longer_than_asked_for_is_read_from_its_last_mebibyte(client, build, file, monkeypatch):
    monkeypatch.setattr(logs, "TAIL_BYTES", len(CONTENT) - 10)
    file.whole = True

    lines = list(client.read_log(PROJECT, build, LogKind.INSTALL, tail=50))

    assert texts(lines) == LINES[1:]
    assert lines[-1].offset == len(CONTENT)


def test_a_tail_of_a_log_of_newlines_holds_no_line_for_each(client, build, file):
    file.content = b"\n" * 200_000
    tracemalloc.start()
    try:
        count = sum(1 for _ in client.read_log(PROJECT, build, LogKind.INSTALL, tail=len(file.content)))
        _, most = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert count == len(file.content)
    assert most < 16 * len(file.content)


def test_reads_a_line_that_no_newline_ends(client, build, file):
    file.content = b"one\ntwo"

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == ["one", "two"]
    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL, tail=1)) == ["two"]


def test_an_empty_log_has_no_line(client, build, file):
    file.content = b""

    assert list(client.read_log(PROJECT, build, LogKind.INSTALL)) == []
    assert list(client.read_log(PROJECT, build, LogKind.INSTALL, tail=3)) == []


def test_lines_are_the_same_however_the_bytes_arrive(client, build, file):
    file.chunk = 7

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == LINES


def test_a_line_longer_than_the_maximum_is_cut_and_marked(client, build, file):
    file.content = b"short\n" + b"x" * (logs.MAX_LINE * 3) + b"\nafter\n"
    file.chunk = 5000

    lines = list(client.read_log(PROJECT, build, LogKind.INSTALL))

    assert [(len(line.text), line.truncated) for line in lines] == [(5, False), (logs.MAX_LINE, True), (5, False)]
    assert lines[1].offset == len(file.content) - 6
    assert lines[2] == LogLine("after", len(file.content), truncated=False)


def test_a_line_exactly_at_the_maximum_is_whole(client, build, file):
    file.content = b"x" * logs.MAX_LINE + b"\n"

    assert [line.truncated for line in client.read_log(PROJECT, build, LogKind.INSTALL)] == [False]


def test_following_from_the_offset_of_a_cut_line_starts_at_the_next_line(client, build, file, time):
    file.content = b"x" * (logs.MAX_LINE * 2) + b"\nafter\n"
    file.chunk = 5000
    cut = next(client.read_log(PROJECT, build, LogKind.INSTALL))

    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60, offset=cut.offset)

    assert next(following).text == "after"


def test_bytes_that_do_not_decode_are_replaced(client, build, file):
    file.content = b"\xe9t\xe9 \xff\nnext\n"

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == ["\ufffdt\ufffd \ufffd", "next"]


def test_a_line_is_returned_unchanged(client, build, file):
    file.content = b"\x1b[31mred\x1b[0m\r\n\n"

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == ["\x1b[31mred\x1b[0m\r", ""]


@pytest.mark.parametrize("kind", [LogKind.UPDATE, LogKind.UNKNOWN, "update", "../list", ""])
def test_a_log_the_build_does_not_have_is_not_found(client, build, file, kind, worker_requests):
    for call in (client.read_log, lambda *args: client.follow_log(*args, timeout=5)):
        with pytest.raises(NotFoundError, match="no log named"):
            call(PROJECT, build, kind)

    assert INSTALL not in [request.url.path for request in worker_requests]


def test_a_listed_name_that_could_leave_its_address_is_a_changed_upstream(client, upstream, build, file):
    upstream.bodies[LIST]["result"][2]["name"] = "../../x"

    with pytest.raises(UpstreamChangedError) as raised:
        client.read_log(PROJECT, build, "../../x")

    assert raised.value.field == "result.name"


def test_a_build_that_waits_for_a_worker_has_no_log(client, file, upstream):
    waiting = client.build(BRANCH, 88240)
    upstream.requests.clear()

    assert client.logs(PROJECT, waiting) == []
    with pytest.raises(NotFoundError, match="waits for a worker"):
        client.read_log(PROJECT, waiting, LogKind.INSTALL)
    assert {request.url.host for request in upstream.requests} == {"www.odoo.sh"}


def test_a_build_that_is_no_longer_listed_is_not_found(client, build, file):
    with pytest.raises(NotFoundError):
        client.logs(PROJECT, dataclasses.replace(build, id=1))


def test_a_project_the_user_cannot_reach_is_not_found(client, upstream, build, file):
    upstream.bodies["/app/project/other/get_info"] = {"error": {"data": {"name": "builtins.Exception"}}}

    with pytest.raises(NotFoundError, match="'other'"):
        client.logs("other", build)


def test_a_project_name_stays_in_its_place_in_the_address(client, upstream, build, file):
    upstream.bodies["/app/project/../../branch/1/rebuild#/get_info"] = upstream.bodies[INFO]

    client.logs("../../branch/1/rebuild#", build)

    assert upstream.requests[-2].url.raw_path == b"/app/project/%2E%2E%2F%2E%2E%2Fbranch%2F1%2Frebuild%23/get_info"


@pytest.mark.parametrize("token", ["", False, None, 7])
def test_a_token_that_is_not_one_is_a_changed_upstream(client, upstream, build, file, token):
    upstream.bodies[INFO]["result"]["access_token"] = token

    with pytest.raises(UpstreamChangedError) as raised:
        client.logs(PROJECT, build)

    assert raised.value.field == "result.access_token"


@pytest.mark.parametrize(
    "worker",
    [
        "http://eupd00.odoo.com",
        "https://eupd00.odoo.com:8443",
        "https://eupd00.odoo.com/",
        "https://eupd00.odoo.com/x",
        "https://user@eupd00.odoo.com",
        "https://a.eupd00.odoo.com",
        "https://eupd00.odoo.com.example.com",
        "https://odoo.com",
        "https://eupd00.odoo.com?x=1",
        "https://eupd00.odoo.com#x",
        "https://EUPD00.odoo.com",
        "https://example.com\\.odoo.com",
        "https://eupd00.odoo.com\n",
        "https://xn--80ak6aa92e.odoo.com",
        "eupd00.odoo.com",
        "",
    ],
)
def test_a_worker_that_is_not_a_host_of_odoo_com_is_asked_nothing(client, upstream, build, file, worker):
    for listed in upstream.bodies[BUILDS]["result"][0]["builds"]:
        listed["worker_url"] = worker
    upstream.requests.clear()

    for call in (client.logs, lambda *args: client.read_log(*args, LogKind.INSTALL)):
        with pytest.raises(UpstreamChangedError) as raised:
            call(PROJECT, build)
        assert raised.value.field == "worker_url"

    assert {request.url.host for request in upstream.requests} == {"www.odoo.sh"}


def test_a_redirect_from_a_worker_is_not_followed(client, upstream, build, file, worker_requests):
    upstream.bodies[INSTALL] = lambda _: httpx2.Response(302, headers={"Location": "https://example.com/"})

    with pytest.raises(UpstreamChangedError) as raised:
        list(client.read_log(PROJECT, build, LogKind.INSTALL))

    assert raised.value.field == "Location"
    assert {request.url.host for request in worker_requests} == {"www.odoo.sh", WORKER}


def test_a_worker_that_sends_to_the_login_does_not_reject_the_session(upstream, backend, session, file):
    backend.entries.clear()
    upstream.bodies[LIST] = lambda _: httpx2.Response(303, headers={"Location": "https://www.odoo.sh/web/login"})
    rejected = []
    transport = upstream.connect(Secret(session), on_rejected=lambda: rejected.append(True))

    with pytest.raises(UpstreamChangedError):
        transport.worker_call("logs", f"https://{WORKER}", LIST, Secret(TOKEN))

    assert rejected == []


@pytest.mark.parametrize(
    ("answer", "error", "field"),
    [
        (httpx2.Response(200, content=CONTENT), UpstreamChangedError, "body"),
        (httpx2.Response(206, content=CONTENT), UpstreamChangedError, "Content-Range"),
        (
            httpx2.Response(206, headers={"Content-Range": "bytes */9"}, content=b"x"),
            UpstreamChangedError,
            "Content-Range",
        ),
        (
            httpx2.Response(206, headers={"Content-Range": "bytes 5-8/9"}, content=b"x"),
            UpstreamChangedError,
            "Content-Range",
        ),
        (httpx2.Response(416), UpstreamChangedError, "status"),
        (httpx2.Response(404), NotFoundError, None),
        (httpx2.Response(403), PermissionDeniedError, None),
    ],
)
def test_an_answer_the_reference_does_not_describe_is_what_it_is(client, upstream, build, file, answer, error, field):
    upstream.bodies[INSTALL] = lambda _: answer

    with pytest.raises(error) as raised:
        list(client.read_log(PROJECT, build, LogKind.INSTALL))

    assert getattr(raised.value, "field", None) == field


@pytest.mark.parametrize("tail", [0, -1])
def test_a_tail_under_one_is_refused_before_any_request(client, build, file, worker_requests, tail):
    with pytest.raises(ValueError, match="tail"):
        client.read_log(PROJECT, build, LogKind.INSTALL, tail=tail)

    assert worker_requests == []


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"timeout": 0}, ValueError),
        ({"timeout": -1}, ValueError),
        ({"timeout": 5, "tail": -1}, ValueError),
        ({"timeout": 5, "tail": "3"}, TypeError),
        ({"timeout": 5, "offset": -1}, ValueError),
        ({"timeout": 5, "offset": 2.0}, TypeError),
    ],
)
def test_follow_refuses_what_it_cannot_use_before_any_request(client, build, file, worker_requests, options, error):
    with pytest.raises(error):
        client.follow_log(PROJECT, build, LogKind.INSTALL, **options)

    assert worker_requests == []


def test_follows_new_lines_one_request_a_second(client, build, file, time):
    file.content = b"old\n"
    added = [b"one\ntw", b"", b"o\nthree\n"]
    time.during = lambda: setattr(file, "content", file.content + added.pop(0))

    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60)
    lines = [next(following) for _ in range(3)]
    following.close()

    assert texts(lines) == ["one", "two", "three"]
    assert [line.offset for line in lines] == [8, 12, 18]
    assert file.ranges == ["bytes=-1", "bytes=3-", "bytes=9-", "bytes=9-"]
    assert time.sleeps == [1.0, 1.0, 1.0]


def test_follows_after_the_last_lines_asked_for(client, build, file, time):
    time.during = lambda: setattr(file, "content", CONTENT + b"new\n")

    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60, tail=2)

    assert texts(next(following) for _ in range(3)) == [*LINES[-2:], "new"]
    assert file.ranges == ["bytes=-1048576", f"bytes={len(CONTENT) - 1}-"]


def test_following_from_the_end_leaves_out_the_line_being_written(client, build, file, time):
    file.content = b"old\nhal"
    time.during = lambda: setattr(file, "content", b"old\nhalf\nnew\n")

    assert next(client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60)).text == "new"


def test_follows_a_log_that_is_empty_at_first(client, build, file, time):
    file.content = b""
    time.during = lambda: setattr(file, "content", b"first\n")

    assert next(client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60)) == LogLine("first", 6, truncated=False)
    assert file.ranges == ["bytes=-1", "bytes=0-"]


def test_follows_from_the_offset_of_a_line_read_before(client, build, file, time):
    second = list(client.read_log(PROJECT, build, LogKind.INSTALL))[1]
    file.ranges.clear()

    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60, offset=second.offset)

    assert texts(next(following) for _ in LINES[2:]) == LINES[2:]
    assert file.ranges == [f"bytes={second.offset - 1}-"]
    assert time.sleeps == []


def test_follows_through_a_dropped_connection_with_every_line_once(client, build, file, time):
    two = len(LINES[0]) + len(LINES[1]) + 2
    file.failures = [two + 10]

    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60, offset=0)

    assert texts(next(following) for _ in LINES) == LINES
    assert file.ranges == ["bytes=0-", f"bytes={two + 9}-"]
    assert time.sleeps == [1.0]


@pytest.mark.parametrize("failure", [httpx2.ConnectError, httpx2.ReadTimeout, 502, 503])
def test_a_failed_request_is_sent_again_from_the_same_place(client, build, file, time, failure):
    file.failures = [failure, failure]

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == LINES
    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL, tail=2)) == LINES[-2:]
    assert file.ranges == ["bytes=0-"] * 3 + ["bytes=-1048576"]
    assert time.sleeps == [1.0, 2.0]


@pytest.mark.parametrize("tail", [None, 2])
def test_gives_up_after_the_third_failure_in_a_row(client, build, file, time, tail):
    file.failures = [httpx2.ConnectError] * 3

    with pytest.raises(UpstreamUnavailableError):
        list(client.read_log(PROJECT, build, LogKind.INSTALL, tail=tail))

    assert len(file.ranges) == 3
    assert time.sleeps == [1.0, 2.0]


def test_a_request_that_brought_something_starts_the_count_of_failures_again(client, build, file, time):
    file.failures = [httpx2.ConnectError, httpx2.ConnectError, 5, httpx2.ConnectError]

    assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == LINES
    assert time.sleeps == [1.0, 2.0, 1.0, 2.0]


def test_a_log_still_followed_at_the_timeout_raises_the_timeout_error(client, build, file, time):
    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=2.5)

    with pytest.raises(StreamTimeoutError) as raised:
        list(following)

    assert not isinstance(raised.value, UpstreamUnavailableError)
    assert raised.value.operation == "logs"
    assert time.sleeps == [1.0, 1.0, 0.5]
    assert len(file.ranges) == 4


def test_a_follow_with_no_limit_asks_every_second_and_raises_nothing(client, build, file, time):
    file.content = b"old\n"
    time.during = lambda: setattr(file, "content", file.content + b"new\n")

    following = client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=math.inf)

    assert texts(next(following) for _ in range(3)) == ["new"] * 3
    following.close()
    assert time.sleeps == [1.0, 1.0, 1.0]


@pytest.mark.parametrize(
    "opened",
    [
        lambda client, build: client.read_log(PROJECT, build, LogKind.INSTALL),
        lambda client, build: client.follow_log(PROJECT, build, LogKind.INSTALL, timeout=60, offset=0),
    ],
    ids=["read", "follow"],
)
def test_closing_early_closes_the_connection_and_asks_nothing_more(client, build, file, time, opened):
    file.chunk = 20
    closed = []
    answer = file.__call__

    def watched(request):
        response = answer(request)
        response.stream.close = lambda: closed.append(True)
        return response

    file.__class__ = type("Watched", (File,), {"__call__": lambda self, request: watched(request)})
    lines = opened(client, build)

    assert next(lines).text == LINES[0]
    assert closed == []
    lines.close()

    assert closed == [True]
    assert file.ranges == ["bytes=0-"]
    assert client.build(BRANCH, BUILD) == build


def test_another_request_can_be_sent_while_a_log_is_open(client, build, file):
    file.chunk = 20
    reading = client.read_log(PROJECT, build, LogKind.INSTALL)
    next(reading)

    assert client.build(BRANCH, BUILD) == build
    assert texts(reading) == LINES[1:]


@pytest.mark.parametrize("failure", [httpx2.ConnectError, 0, 500, 404, 302])
def test_an_error_and_the_library_log_hold_neither_the_token_nor_the_log(client, build, file, time, caplog, failure):
    with caplog.at_level(logging.DEBUG):
        assert texts(client.read_log(PROJECT, build, LogKind.INSTALL)) == LINES
        file.failures = [failure] * 3
        with pytest.raises(OdoucheError) as raised:
            list(client.read_log(PROJECT, build, LogKind.INSTALL))

    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    shown = [str(raised.value), repr(raised.value), "".join(traceback.format_exception(raised.value))]
    shown += [f"{record.getMessage()} {record.args}" for record in caplog.records]
    assert f"GET {INSTALL}: " in "".join(shown)
    assert f"HTTP Request: GET https://{WORKER}{INSTALL} " in "".join(shown)
    for text in shown:
        assert TOKEN not in text
        assert "odoo.modules.loading" not in text


def test_a_token_is_not_shown(client, build, file, monkeypatch):
    seen = []
    worker_call = type(client._transport).worker_call
    monkeypatch.setattr(type(client._transport), "worker_call", lambda *args: seen.append(args) or worker_call(*args))

    client.logs(PROJECT, build)

    assert TOKEN not in repr(seen)
    assert seen[0][-1].expose_secret() == TOKEN
