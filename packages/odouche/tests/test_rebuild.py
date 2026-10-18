import copy
import inspect
import json
import traceback

import httpx2
import pytest

from odouche import (
    Client,
    NotFoundError,
    OutcomeUnknownError,
    PermissionDeniedError,
    ReadOnlyError,
    Secret,
    SessionExpiredError,
    Stage,
    StageRefusedError,
    UpstreamUnavailableError,
)
from odouche._upstream import rebuild
from odouche._upstream.transport import state_changing


BRANCH = 51044
BUILDS = "/app/branch/51044/builds"
PATH = "/app/branch/51044/rebuild"
NEW = 88301


def listed(upstream):
    return upstream.bodies[BUILDS]["result"][0]["builds"]


def paths(upstream):
    return [request.url.path for request in upstream.requests]


@pytest.fixture(autouse=True)
def rebuilds(upstream):
    """Answer a rebuild as Odoo.sh does, with a new build on the branch."""

    def answer(_):
        listed(upstream).insert(0, copy.deepcopy(listed(upstream)[0]) | {"id": NEW})
        return httpx2.Response(200, json=upstream.load("rebuild.json"))

    upstream.bodies[PATH] = answer


def in_stage(upstream, stage):
    upstream.bodies[BUILDS]["result"][0]["branch_info"]["stage"] = stage


def test_sends_the_documented_request_once_and_returns_the_new_build(client, upstream, session):
    build = client.rebuild(BRANCH)

    assert build.id == NEW
    assert build == client.latest_build(BRANCH)
    assert paths(upstream)[:3] == [BUILDS, PATH, BUILDS]
    request = upstream.requests[1]
    assert request.method == "POST"
    assert json.loads(request.content) == {"jsonrpc": "2.0", "method": "call", "params": {}, "id": 1}
    assert request.headers["Cookie"] == f"session_id={session}"


def test_takes_a_branch_or_its_number(client, upstream):
    branch = client.branches("acme-shop")[1]

    assert client.rebuild(branch).id == NEW
    assert paths(upstream).count(PATH) == 1


def test_a_branch_with_no_build_is_rebuilt(client, upstream):
    listed(upstream).clear()
    upstream.bodies[PATH] = lambda _: (
        listed(upstream).append(upstream.load("build_progress.json")["result"][0]["builds"][0]),
        httpx2.Response(200, json=upstream.load("rebuild.json")),
    )[1]

    assert client.rebuild(BRANCH) == client.latest_build(BRANCH)


def test_a_client_is_not_read_only_unless_asked(client, upstream, session):
    assert client.read_only is False
    assert Client(Secret(session), read_only=True).read_only is True


def test_a_read_only_client_sends_nothing(upstream, session):
    client = Client(Secret(session), read_only=True)

    with pytest.raises(ReadOnlyError) as raised:
        client.rebuild(BRANCH)

    assert raised.value.operation == "rebuild"
    assert upstream.requests == []


def test_a_read_only_client_still_reads(upstream, session):
    assert Client(Secret(session), read_only=True).builds(BRANCH)[0].id == 88240


def test_a_read_only_transport_refuses_a_change_by_itself(upstream, session):
    transport = upstream.connect(Secret(session), read_only=True)

    with pytest.raises(ReadOnlyError):
        transport.change("rebuild", PATH)

    assert upstream.requests == []


def test_the_rebuild_is_marked_as_state_changing(upstream, session):
    assert inspect.unwrap(rebuild.rebuild) is not rebuild.rebuild
    assert state_changing(lambda _: "done")(upstream.connect(Secret(session))) == "done"


@pytest.mark.parametrize("failure", [503, 500, httpx2.ReadTimeout, httpx2.WriteTimeout, httpx2.RemoteProtocolError])
def test_a_rebuild_that_is_not_confirmed_is_sent_once_and_its_outcome_is_unknown(client, upstream, failure):
    upstream.failures[PATH] = failure

    with pytest.raises(OutcomeUnknownError, match="Look before trying again") as raised:
        client.rebuild(BRANCH)

    assert raised.value.operation == "rebuild"
    assert not isinstance(raised.value, UpstreamUnavailableError)
    assert paths(upstream) == [BUILDS, PATH]


@pytest.mark.parametrize("failure", [httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout])
def test_a_rebuild_that_did_not_leave_is_not_done_and_not_repeated(client, upstream, failure):
    upstream.failures[PATH] = failure

    with pytest.raises(UpstreamUnavailableError):
        client.rebuild(BRANCH)

    assert paths(upstream).count(PATH) == 1


def test_a_refused_rebuild_is_not_repeated(client, upstream):
    upstream.failures[PATH] = 403

    with pytest.raises(PermissionDeniedError):
        client.rebuild(BRANCH)

    assert paths(upstream) == [BUILDS, PATH]


def test_a_rebuild_with_no_new_build_is_unknown(client, upstream):
    upstream.bodies[PATH] = upstream.load("rebuild.json")

    with pytest.raises(OutcomeUnknownError, match="was sent"):
        client.rebuild(BRANCH)

    assert paths(upstream).count(PATH) == 1


@pytest.mark.parametrize("builds", [503, 403, {"result": []}, "unauthenticated.json"])
def test_a_rebuild_whose_build_cannot_be_read_is_unknown(client, upstream, builds):
    def answer(_):
        if isinstance(builds, int):
            upstream.failures[BUILDS] = builds
        else:
            upstream.bodies[BUILDS] = upstream.load(builds) if isinstance(builds, str) else builds
        return httpx2.Response(200, json=upstream.load("rebuild.json"))

    upstream.bodies[PATH] = answer

    with pytest.raises(OutcomeUnknownError, match="was sent") as raised:
        client.rebuild(BRANCH)

    assert raised.value.__context__ is None
    assert paths(upstream).count(PATH) == 1


def test_a_production_branch_is_refused_before_any_request(client, upstream):
    production = client.branches("acme-shop")[0]
    upstream.requests.clear()

    with pytest.raises(StageRefusedError, match="production") as raised:
        client.rebuild(production)

    assert production.stage is Stage.PRODUCTION
    assert raised.value.operation == "rebuild"
    assert upstream.requests == []


def test_a_check_refuses_the_stage_a_rebuild_refuses_and_asks_nothing(client, upstream):
    production, development = client.branches("acme-shop")[:2]
    upstream.requests.clear()

    client.check_rebuild(development)
    with pytest.raises(StageRefusedError) as checked:
        client.check_rebuild(production)
    with pytest.raises(StageRefusedError) as rebuilt:
        client.rebuild(production)

    assert str(checked.value) == str(rebuilt.value)
    assert checked.value.operation == "rebuild"
    assert upstream.requests == []


def test_a_read_only_client_checks_too(upstream, session):
    client = Client(Secret(session), read_only=True)
    production, development = client.branches("acme-shop")[:2]
    upstream.requests.clear()

    client.check_rebuild(development)
    with pytest.raises(StageRefusedError):
        client.check_rebuild(production)

    assert upstream.requests == []


@pytest.mark.parametrize("stage", ["production", "archived"])
def test_a_branch_given_by_number_is_refused_before_the_rebuild(client, upstream, stage):
    in_stage(upstream, stage)

    with pytest.raises(StageRefusedError):
        client.rebuild(BRANCH)

    assert paths(upstream) == [BUILDS]


def test_a_branch_that_changed_stage_since_it_was_read_is_refused(client, upstream):
    branch = client.branches("acme-shop")[1]
    in_stage(upstream, "production")

    with pytest.raises(StageRefusedError):
        client.rebuild(branch)

    assert PATH not in paths(upstream)


def test_a_staging_branch_is_rebuilt(client, upstream):
    in_stage(upstream, "staging")

    assert client.rebuild(BRANCH).id == NEW


@pytest.mark.parametrize("branch", ["51044", "1/builds#", True, 51044.0, None])
def test_a_branch_that_is_not_a_number_is_refused_before_any_request(client, upstream, branch):
    with pytest.raises(TypeError, match="branch"):
        client.rebuild(branch)

    assert upstream.requests == []


REFUSAL = {"jsonrpc": "2.0", "id": None, "error": {"code": 200, "data": {"name": "builtins.Exception"}}}


def test_a_branch_the_user_cannot_reach_is_not_found_before_the_rebuild(client, upstream):
    upstream.bodies["/app/branch/1/builds"] = REFUSAL

    with pytest.raises(NotFoundError):
        client.rebuild(1)

    assert paths(upstream) == ["/app/branch/1/builds"]


def test_a_rebuild_odoo_sh_refuses_without_a_reason_is_unknown(client, upstream):
    upstream.bodies[PATH] = REFUSAL

    with pytest.raises(OutcomeUnknownError):
        client.rebuild(BRANCH)

    assert paths(upstream) == [BUILDS, PATH]


def test_a_rejected_session_is_expired_and_the_rebuild_is_not_repeated(client, upstream):
    upstream.bodies[PATH] = upstream.load("unauthenticated.json")

    with pytest.raises(SessionExpiredError):
        client.rebuild(BRANCH)

    assert paths(upstream).count(PATH) == 1


def test_an_unknown_outcome_shows_nothing_of_the_request(client, upstream, session):
    upstream.failures[PATH] = httpx2.ReadTimeout

    with pytest.raises(OutcomeUnknownError) as raised:
        client.rebuild(BRANCH)

    assert raised.value.__context__ is None
    assert session not in "".join(traceback.format_exception(raised.value))
