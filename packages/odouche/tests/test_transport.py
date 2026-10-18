import inspect
import json
import logging
import traceback
from pathlib import Path

import httpx2
import pytest

from odouche import (
    NotFoundError,
    OdoucheError,
    PermissionDeniedError,
    Secret,
    SessionExpiredError,
    UpstreamChangedError,
    UpstreamUnavailableError,
    __version__,
)
from odouche._upstream.transport import HOST, Transport, is_sendable, is_unauthenticated


SESSION = "s3ss10n-v4lu3"
OPERATION = "projects"
PATH = "/app/projects"
FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = Path(__file__).parents[1] / "src" / "odouche"

UNAUTHENTICATED = json.loads((FIXTURES / "unauthenticated.json").read_text())
LOGIN = "/web/login?redirect=%2Fproject%3F"
LOGOUT = "/web/session/logout"
WORKER = "https://eupd00.odoo.com"


class Upstream:
    """Answers each request with the next of `answers`, repeating the last, and keeps the requests."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.rejected = 0
        self.sleeps = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, type):
            raise answer(f"failed, Cookie: {request.headers['Cookie']}", request=request)
        return answer

    def reject(self):
        self.rejected += 1


def fixture(name, status=200):
    return httpx2.Response(status, content=(FIXTURES / name).read_bytes())


def redirect(location, status=303):
    return httpx2.Response(status, headers={"Location": location})


@pytest.fixture
def connect():
    transports = []

    def connect(*answers):
        upstream = Upstream(*answers)
        transports.append(
            Transport(
                Secret(SESSION),
                on_rejected=upstream.reject,
                transport=httpx2.MockTransport(upstream),
                sleep=upstream.sleeps.append,
            )
        )
        return transports[-1], upstream

    yield connect
    for transport in transports:
        transport.close()


def test_sends_the_json_rpc_request_with_the_session(connect):
    transport, upstream = connect(fixture("projects.json"))

    answer = transport.call(OPERATION, PATH, {"build_limit": 4})

    (request,) = upstream.requests
    assert request.method == "POST"
    assert str(request.url) == f"https://{HOST}{PATH}"
    assert json.loads(request.content) == {"jsonrpc": "2.0", "method": "call", "params": {"build_limit": 4}, "id": 1}
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Cookie"] == f"session_id={SESSION}"
    assert request.headers["User-Agent"] == f"odouche/{__version__}"
    assert answer == json.loads((FIXTURES / "projects.json").read_text())


def test_returns_an_answer_that_has_no_result(connect):
    transport, _ = connect(fixture("rebuild.json"))

    assert "result" not in transport.call("rebuild", "/app/branch/1/rebuild")


def test_leaves_with_a_get_that_carries_the_session_and_follows_nothing(connect):
    transport, upstream = connect(redirect("/"))

    assert transport.leave("logout", LOGOUT, {"redirect": "/"}, to="/") is None

    (request,) = upstream.requests
    assert request.method == "GET"
    assert str(request.url) == f"https://{HOST}{LOGOUT}?redirect=%2F"
    assert request.content == b""
    assert request.headers["Cookie"] == f"session_id={SESSION}"


def test_leaving_without_params_keeps_the_query_of_the_path(connect):
    transport, upstream = connect(redirect("/"))

    transport.leave("logout", f"{LOGOUT}?redirect=%2F", to="/")

    assert str(upstream.requests[0].url) == f"https://{HOST}{LOGOUT}?redirect=%2F"


@pytest.mark.parametrize("location", ["/", f"https://{HOST}/", f"https://{HOST}/?db=x"])
def test_leaves_when_sent_where_it_expects(connect, location):
    transport, _ = connect(redirect(location))

    transport.leave("logout", LOGOUT, to="/")


@pytest.mark.parametrize(
    "answer",
    [
        redirect("https://evil.example.com/"),
        redirect(f"http://{HOST}/"),
        redirect("/web/session/confirm"),
        httpx2.Response(303),
    ],
)
def test_leaving_for_elsewhere_is_a_changed_upstream(connect, answer):
    transport, _ = connect(answer)

    with pytest.raises(UpstreamChangedError) as raised:
        transport.leave("logout", LOGOUT, to="/")

    assert raised.value.field == "Location"


def test_leaving_with_a_rejected_session_is_a_rejection(connect):
    transport, upstream = connect(redirect(LOGIN))

    with pytest.raises(SessionExpiredError):
        transport.leave("logout", LOGOUT, to="/")

    assert upstream.rejected == 1


@pytest.mark.parametrize(
    ("answer", "error", "field"),
    [
        (httpx2.Response(200), UpstreamChangedError, "status"),
        (redirect("/", 302), UpstreamChangedError, "Location"),
        (httpx2.Response(404), NotFoundError, None),
        (httpx2.Response(500), UpstreamUnavailableError, None),
        (httpx2.ConnectError, UpstreamUnavailableError, None),
    ],
)
def test_leaving_raises_on_any_answer_but_the_one_it_knows(connect, answer, error, field):
    transport, upstream = connect(answer)

    with pytest.raises(error) as raised:
        transport.leave("logout", LOGOUT, to="/")

    assert raised.value.operation == "logout"
    assert getattr(raised.value, "field", None) == field
    assert len(upstream.requests) == 1
    assert SESSION not in "".join(traceback.format_exception(raised.value))


def test_leaving_is_retried_when_asked(connect):
    transport, upstream = connect(httpx2.Response(502), httpx2.ConnectError, redirect("/"))

    transport.leave("logout", LOGOUT, retry=True, to="/")

    assert len(upstream.requests) == 3
    assert upstream.sleeps == [1.0, 2.0]


def test_leaves_only_to_the_host(connect):
    transport, upstream = connect(redirect("/"))

    with pytest.raises(ValueError, match="absolute"):
        transport.leave("logout", "//evil.example.com/x", to="/")

    assert upstream.requests == []


def test_closes_as_a_context_manager():
    with Transport(Secret(SESSION), transport=httpx2.MockTransport(Upstream(fixture("projects.json")))) as transport:
        transport.call(OPERATION, PATH)

    assert transport._client.is_closed


@pytest.mark.parametrize("path", ["//evil.example.com/x", "https://evil.example.com/x", "@evil.example.com/x", ""])
def test_refuses_a_path_that_could_leave_the_host(connect, path):
    transport, upstream = connect(fixture("projects.json"))

    with pytest.raises(ValueError, match=HOST):
        transport.call(OPERATION, path)

    assert upstream.requests == []


@pytest.mark.parametrize("path", ["//evil.example.com/x", "@evil.example.com/x", ".evil.example.com/x", ":8443/x", ""])
def test_refuses_a_path_that_could_leave_the_worker(connect, path):
    transport, upstream = connect(fixture("build_logs_list.json"))

    with pytest.raises(ValueError, match="worker"):
        transport.worker_call("logs", WORKER, path, Secret("t0k3n"))
    with (
        pytest.raises(ValueError, match="worker"),
        transport.worker_read("logs", WORKER, path, Secret("t0k3n"), "bytes=0-"),
    ):
        pass

    assert upstream.requests == []


def test_a_worker_is_asked_without_the_session(connect):
    transport, upstream = connect(fixture("build_logs_list.json"))

    transport.worker_call("logs", WORKER, "/paas/build/1/logs/list", Secret("t0k3n"))

    (request,) = upstream.requests
    assert str(request.url) == f"{WORKER}/paas/build/1/logs/list"
    assert json.loads(request.content)["params"] == {"token": "t0k3n"}
    assert "Cookie" not in request.headers


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/web/login",
        "http://www.odoo.sh/web/login",
        "https://www.odoo.sh:8443/web/login",
        "/project",
    ],
)
def test_does_not_follow_a_redirect(connect, status, location):
    transport, upstream = connect(redirect(location, status))

    with pytest.raises(UpstreamChangedError) as raised:
        transport.call(OPERATION, PATH)

    assert raised.value.field == "Location"
    assert raised.value.status == status
    assert len(upstream.requests) == 1
    assert upstream.rejected == 0


def test_a_redirect_to_no_address_is_a_failed_request(connect):
    transport, upstream = connect(redirect("http://[::1"))

    with pytest.raises(UpstreamUnavailableError):
        transport.call(OPERATION, PATH)

    assert len(upstream.requests) == 1
    assert upstream.rejected == 0


@pytest.mark.parametrize("answer", [fixture("unauthenticated.json"), redirect(LOGIN)], ids=["json", "page"])
def test_a_rejected_session_is_discarded(connect, answer):
    transport, upstream = connect(answer)

    with pytest.raises(SessionExpiredError) as raised:
        transport.call(OPERATION, PATH, retry=True)

    assert raised.value.operation == OPERATION
    assert upstream.rejected == 1
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    ("status", "location", "body", "expected"),
    [
        (200, None, UNAUTHENTICATED, True),
        (303, LOGIN, None, True),
        (303, f"https://{HOST}/web/login", None, True),
        (200, None, {"jsonrpc": "2.0", "id": 1, "result": {}}, False),
        (200, None, {"error": {"data": {"name": "odoo.exceptions.AccessError"}}}, False),
        (200, None, {"error": "Session expired"}, False),
        (200, None, {"error": {"data": "Session expired"}}, False),
        (200, None, [UNAUTHENTICATED], False),
        (500, None, UNAUTHENTICATED, False),
        (303, None, None, False),
        (303, "/", None, False),
        (303, "https://evil.example.com/web/login", None, False),
        (303, f"https://{HOST}:8443/web/login", None, False),
        (303, "http://[::1", None, False),
        (302, LOGIN, None, False),
    ],
)
def test_is_unauthenticated(status, location, body, expected):
    assert is_unauthenticated(status, location, body) is expected


@pytest.mark.parametrize(
    ("answer", "cls", "status"),
    [
        (fixture("not_found.html", 404), NotFoundError, 404),
        (httpx2.Response(403), PermissionDeniedError, 403),
        (fixture("access_error.json"), PermissionDeniedError, 200),
        (httpx2.Response(429), UpstreamUnavailableError, 429),
        (httpx2.Response(500), UpstreamUnavailableError, 500),
        (httpx2.Response(503), UpstreamUnavailableError, 503),
        (httpx2.ConnectError, UpstreamUnavailableError, None),
        (httpx2.ReadTimeout, UpstreamUnavailableError, None),
        (httpx2.DecodingError, UpstreamUnavailableError, None),
    ],
)
def test_maps_a_failure_to_a_library_error(connect, answer, cls, status):
    transport, upstream = connect(answer)

    with pytest.raises(cls) as raised:
        transport.call(OPERATION, PATH)

    assert raised.value.operation == OPERATION
    assert raised.value.status == status
    assert len(upstream.requests) == 1
    assert upstream.rejected == 0


@pytest.mark.parametrize(
    ("answer", "field"),
    [
        (httpx2.Response(401), "status"),
        (httpx2.Response(204), "status"),
        (httpx2.Response(304), "Location"),
        (fixture("not_found.html"), "body"),
        (httpx2.Response(200, content=b"\xff"), "body"),
        (httpx2.Response(200, json=[1]), "body"),
        (httpx2.Response(200, json={"error": {"data": {"name": "builtins.Exception"}}}), "error.data.name"),
    ],
)
def test_reports_an_answer_the_reference_does_not_describe(connect, answer, field):
    transport, upstream = connect(answer)

    with pytest.raises(UpstreamChangedError) as raised:
        transport.call(OPERATION, PATH)

    assert raised.value.operation == OPERATION
    assert raised.value.field == field
    assert upstream.rejected == 0


UNEXPLAINED = {"error": {"data": {"name": "builtins.Exception", "message": "error code: SH-5ecr3t"}}}


def test_an_unexplained_error_is_not_found_where_the_request_says_so(connect):
    transport, upstream = connect(httpx2.Response(200, json=UNEXPLAINED))

    with pytest.raises(NotFoundError, match="No such branch") as raised:
        transport.call(OPERATION, PATH, not_found="No such branch.")

    assert (raised.value.operation, raised.value.status) == (OPERATION, 200)
    assert upstream.rejected == 0


@pytest.mark.parametrize(
    ("answer", "cls", "rejected"),
    [
        (fixture("access_error.json"), PermissionDeniedError, 0),
        (fixture("unauthenticated.json"), SessionExpiredError, 1),
        (
            httpx2.Response(200, json={"error": {"data": {"name": "odoo.exceptions.UserError"}}}),
            UpstreamChangedError,
            0,
        ),
        (httpx2.Response(500, json=UNEXPLAINED), UpstreamUnavailableError, 0),
    ],
)
def test_any_other_error_is_what_it_is_without_the_option(connect, answer, cls, rejected):
    transport, upstream = connect(answer)

    with pytest.raises(cls):
        transport.call(OPERATION, PATH, not_found="No such branch.")

    assert upstream.rejected == rejected


@pytest.mark.parametrize(
    "answer",
    [
        fixture("unauthenticated.json"),
        fixture("access_error.json"),
        httpx2.Response(200, json=UNEXPLAINED),
        redirect("https://evil.example.com/"),
        httpx2.Response(404),
        httpx2.Response(502),
        httpx2.ConnectError,
        httpx2.ReadTimeout,
    ],
)
def test_an_error_carries_nothing_of_the_request(connect, answer):
    transport, _ = connect(answer)

    with pytest.raises(OdoucheError) as raised:
        transport.call(OPERATION, PATH, not_found="No such branch.")

    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    for text in (str(raised.value), repr(raised.value), "".join(traceback.format_exception(raised.value))):
        assert SESSION not in text
        assert "Cookie" not in text
        assert "octo-dev" not in text
        assert "SH-5ecr3t" not in text


def test_logs_the_request_without_the_session(connect, caplog):
    transport, _ = connect(fixture("projects.json"), httpx2.Response(502), httpx2.ReadTimeout)

    with caplog.at_level(logging.DEBUG, logger="odouche"):
        transport.call(OPERATION, f"{PATH}?token=t0k3n")
        for _ in range(2):
            with pytest.raises(UpstreamUnavailableError):
                transport.call(OPERATION, PATH)

    assert [record.name for record in caplog.records] == ["odouche"] * 3
    assert [record.levelno for record in caplog.records] == [logging.DEBUG] * 3
    assert [message.split(" in ")[0] for message in caplog.messages] == [
        f"POST {PATH}: 200",
        f"POST {PATH}: 502",
        f"POST {PATH}: ReadTimeout",
    ]
    for record in caplog.records:
        assert SESSION not in f"{record.getMessage()} {record.args}"
        assert "t0k3n" not in record.getMessage()


def test_attaches_no_log_handler():
    assert logging.getLogger("odouche").handlers == []


@pytest.mark.parametrize(
    "failure", [httpx2.Response(502), httpx2.Response(503), httpx2.Response(504), httpx2.ReadTimeout]
)
def test_retries_a_read_after_a_pause(connect, failure):
    transport, upstream = connect(failure, failure, fixture("projects.json"))

    assert "result" in transport.call(OPERATION, PATH, retry=True)
    assert upstream.sleeps == [1.0, 2.0]
    assert len(upstream.requests) == 3


def test_stops_retrying_after_the_third_attempt(connect):
    transport, upstream = connect(httpx2.Response(503))

    with pytest.raises(UpstreamUnavailableError):
        transport.call(OPERATION, PATH, retry=True)

    assert upstream.sleeps == [1.0, 2.0]
    assert len(upstream.requests) == 3


@pytest.mark.parametrize("answer", [httpx2.Response(500), httpx2.Response(429), httpx2.Response(404)])
def test_does_not_retry_an_answer_that_would_not_change(connect, answer):
    transport, upstream = connect(answer)

    with pytest.raises(OdoucheError):
        transport.call(OPERATION, PATH, retry=True)

    assert upstream.sleeps == []
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("failure", [httpx2.Response(503), httpx2.ReadTimeout])
def test_never_repeats_a_request_unless_asked(connect, failure):
    transport, upstream = connect(failure, fixture("rebuild.json"))

    with pytest.raises(UpstreamUnavailableError):
        transport.call("rebuild", "/app/branch/1/rebuild")

    assert upstream.sleeps == []
    assert len(upstream.requests) == 1


def test_keeps_no_cookie_from_an_answer(connect):
    renewed = httpx2.Response(
        200,
        headers={"Set-Cookie": f"session_id={SESSION}; Path=/; HttpOnly; Max-Age=691200"},
        content=(FIXTURES / "projects.json").read_bytes(),
    )
    transport, upstream = connect(renewed)

    transport.call(OPERATION, PATH)
    transport.call(OPERATION, PATH)

    assert len(transport._client.cookies.jar) == 0
    assert upstream.requests[1].headers.get_list("Cookie") == [f"session_id={SESSION}"]


def test_a_call_with_seconds_left_cuts_every_phase_to_them(connect):
    transport, upstream = connect(fixture("projects.json"), fixture("projects.json"), fixture("projects.json"))

    transport.call(OPERATION, PATH)
    transport.call(OPERATION, PATH, within=4)
    transport.call(OPERATION, PATH, within=20)

    assert [request.extensions["timeout"] for request in upstream.requests] == [
        {"connect": 10, "read": 30, "write": 10, "pool": 10},
        {"connect": 4, "read": 4, "write": 4, "pool": 4},
        {"connect": 10, "read": 20, "write": 10, "pool": 10},
    ]


def test_a_call_is_not_sent_again_past_the_seconds_left(connect):
    transport, upstream = connect(httpx2.Response(503), fixture("projects.json"))

    with pytest.raises(UpstreamUnavailableError):
        transport.call(OPERATION, PATH, retry=True, within=0.5)

    assert len(upstream.requests) == 1
    assert upstream.sleeps == []


def test_a_call_sent_again_has_what_the_wait_left(connect):
    transport, upstream = connect(httpx2.Response(503), fixture("projects.json"))

    transport.call(OPERATION, PATH, retry=True, within=5)

    assert upstream.sleeps == [1.0]
    assert 3.5 < upstream.requests[1].extensions["timeout"]["read"] <= 4


def test_bounds_every_phase_of_a_request_and_takes_no_option_to_change_it(connect):
    transport, _ = connect(fixture("projects.json"))

    timeout = transport._client.timeout
    assert None not in (timeout.connect, timeout.read, timeout.write, timeout.pool)
    assert not transport._client.follow_redirects
    assert set(inspect.signature(Transport).parameters) == {"session", "on_rejected", "read_only", "transport", "sleep"}


@pytest.mark.parametrize(
    ("value", "sendable"),
    [
        (SESSION, True),
        ("A-Za-z0-9_-", True),
        ("", False),
        ("a b", False),
        (f"{SESSION}; tz=UTC", False),
        (f"{SESSION}\r\nX-Injected: 1", False),
        (f"{SESSION}\n", False),
    ],
)
def test_tells_a_session_from_what_would_be_more_than_a_cookie_value(value, sendable):
    assert is_sendable(Secret(value)) is sendable


def test_the_session_is_exposed_only_where_it_is_sent_or_stored():
    exposing = sorted(
        str(path.relative_to(SOURCES))
        for path in SOURCES.rglob("*.py")
        if "expose_secret(" in path.read_text(encoding="utf-8")
    )

    assert exposing == ["_session.py", "_upstream/transport.py", "secret.py"]
