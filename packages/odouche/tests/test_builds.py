import copy
import dataclasses
import json
import logging
import traceback
from datetime import UTC, datetime

import pytest

from odouche import Build, BuildResult, BuildStatus, Commit, NotFoundError, UpstreamChangedError


PATH = "/app/branch/51044/builds"
BRANCH = 51044
MESSAGE = "[FIX] invoicing: round the tax base per line\n\nThe total was rounded once, so lines did not add up."


def listed(upstream):
    return upstream.bodies[PATH]["result"][0]["builds"]


def test_lists_the_builds_newest_first_with_status_result_and_commit(client, upstream, session):
    listed(upstream).reverse()

    builds = client.builds(BRANCH)

    assert [build.id for build in builds] == [88240, 88212, 88150, 88122]
    assert [(build.status, build.result) for build in builds] == [
        (BuildStatus.PROGRESS, None),
        (BuildStatus.DROPPED, BuildResult.SUCCESS),
        (BuildStatus.DROPPED, BuildResult.SUCCESS),
        (BuildStatus.DROPPED, BuildResult.FAILED),
    ]
    assert builds[1] == Build(
        id=88212,
        name="acme-shop-feature-invoicing-88212",
        branch_id=51044,
        branch_name="feature-invoicing",
        commit=Commit(
            hash="3f2a9c1e5b7d4f608192a3b4c5d6e7f801234567",
            message=MESSAGE,
            author="Jane Doe",
            timestamp=datetime(2026, 9, 30, 15, 42, 10, tzinfo=UTC),
            url="https://github.com/Acme-Corp/Odoo-Addons/commit/3f2a9c1e5b7d4f608192a3b4c5d6e7f801234567",
        ),
        status=BuildStatus.DROPPED,
        status_name="dropped",
        result=BuildResult.SUCCESS,
        result_name="success",
        status_info="done",
        started_at=datetime(2026, 9, 30, 15, 43, 2, tzinfo=UTC),
        url="https://acme-shop-feature-invoicing-88212.dev.odoo.com",
    )
    assert [request.url.path for request in upstream.requests] == [PATH]
    assert json.loads(upstream.requests[0].content)["params"] == {"build_limit": 4}
    assert upstream.requests[0].headers["Cookie"] == f"session_id={session}"


def test_what_odoo_sh_leaves_out_of_a_waiting_build_is_none(client):
    build = client.builds(BRANCH)[0]

    assert (build.result, build.result_name, build.status_info, build.started_at) == (None, None, None, None)


def test_takes_a_branch_or_its_number(client):
    branch = client.branches("acme-shop")[1]

    assert client.builds(branch) == client.builds(branch.id)


@pytest.mark.parametrize("branch", ["51044", "1/rebuild#", "../project/p/get_info#", True, 51044.0, None])
def test_a_branch_that_is_not_a_number_is_refused_before_any_request(client, upstream, branch):
    for call in (client.builds, client.latest_build, lambda branch: client.build(branch, 88240)):
        with pytest.raises(TypeError, match="branch"):
            call(branch)

    assert upstream.requests == []


@pytest.mark.parametrize("limit", ["2", 2.0, True, None])
def test_a_limit_that_is_not_a_number_is_refused_before_any_request(client, upstream, limit):
    with pytest.raises(TypeError, match="limit"):
        client.builds(BRANCH, limit=limit)

    assert upstream.requests == []


@pytest.mark.parametrize("build", ["88240", 88240.0, True])
def test_a_build_that_is_not_a_number_is_refused_before_any_request(client, upstream, build):
    with pytest.raises(TypeError, match="build"):
        client.build(BRANCH, build)

    assert upstream.requests == []


def test_a_build_is_immutable(client):
    build = client.builds(BRANCH)[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        build.status = BuildStatus.DONE
    with pytest.raises(dataclasses.FrozenInstanceError):
        build.commit.author = "other"


@pytest.mark.parametrize(
    ("fixture", "status", "result", "finished"),
    [
        ("build_progress.json", BuildStatus.PROGRESS, None, False),
        ("build_done.json", BuildStatus.DONE, BuildResult.SUCCESS, True),
    ],
)
def test_a_build_seen_in_each_status_knows_whether_it_is_finished(client, upstream, fixture, status, result, finished):
    upstream.bodies[PATH] = upstream.load(fixture)

    build = client.latest_build(BRANCH)

    assert (build.status, build.result, build.finished) == (status, result, finished)


@pytest.mark.parametrize(
    ("name", "status", "finished"),
    [
        ("progress", BuildStatus.PROGRESS, False),
        ("updating", BuildStatus.UPDATING, False),
        ("done", BuildStatus.DONE, True),
        ("dropped", BuildStatus.DROPPED, True),
        ("skipped", BuildStatus.SKIPPED, True),
        ("killed", BuildStatus.KILLED, True),
    ],
)
def test_finished_follows_the_status(client, upstream, name, status, finished):
    listed(upstream)[0]["status"] = name

    build = client.builds(BRANCH)[0]

    assert (build.status, build.status_name, build.finished) == (status, name, finished)


def test_an_unseen_status_is_unknown_and_not_finished(client, upstream, caplog):
    listed(upstream)[1]["status"] = "archived"

    with caplog.at_level(logging.DEBUG, logger="odouche"):
        build = client.builds(BRANCH)[1]

    assert (build.status, build.status_name, build.finished) == (BuildStatus.UNKNOWN, "archived", False)
    assert "Unknown build status: 'archived'" in caplog.messages


def test_an_unseen_result_is_unknown_and_keeps_its_name(client, upstream, caplog):
    listed(upstream)[1]["result"] = "flaky"

    with caplog.at_level(logging.DEBUG, logger="odouche"):
        build = client.builds(BRANCH)[1]

    assert (build.result, build.result_name, build.finished) == (BuildResult.UNKNOWN, "flaky", True)
    assert "Unknown build result: 'flaky'" in caplog.messages


def test_a_limit_under_the_answer_is_respected_in_one_request(client, upstream):
    for number in (88300, 88301):
        listed(upstream).append(copy.deepcopy(listed(upstream)[0]) | {"id": number})

    builds = client.builds(BRANCH, limit=5)

    assert [build.id for build in builds] == [88301, 88300, 88240, 88212, 88150]
    assert len(upstream.requests) == 1
    assert json.loads(upstream.requests[0].content)["params"] == {"build_limit": 5}


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_under_one_is_refused_before_any_request(client, upstream, limit):
    with pytest.raises(ValueError, match="at least 1"):
        client.builds(BRANCH, limit=limit)

    assert upstream.requests == []


def test_no_build_is_an_empty_list_and_no_latest_build(client, upstream):
    listed(upstream).clear()

    assert client.builds(BRANCH) == []
    assert client.latest_build(BRANCH) is None


def test_the_latest_build_is_asked_for_alone(client, upstream):
    assert client.latest_build(BRANCH).id == 88240
    assert json.loads(upstream.requests[0].content)["params"] == {"build_limit": 1}


def test_reads_one_build_among_the_latest(client):
    assert client.build(BRANCH, 88150) == client.builds(BRANCH)[2]


def test_a_build_that_is_not_among_the_latest_is_not_found(client):
    with pytest.raises(NotFoundError) as raised:
        client.build(BRANCH, 1)

    assert raised.value.operation == "builds"


def test_a_branch_odoo_sh_refuses_without_a_reason_is_not_found(client, upstream):
    upstream.bodies["/app/branch/1/builds"] = {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": 200, "message": "Odoo Server Error", "data": {"name": "builtins.Exception"}},
    }

    with pytest.raises(NotFoundError) as raised:
        client.builds(1)

    assert raised.value.operation == "builds"
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "name",
        "branch_id",
        "status",
        "result",
        "status_info",
        "start_datetime",
        "url",
        "head_commit_author",
        "head_commit_msg",
        "head_commit_timestamp",
        "head_commit_url",
    ],
)
@pytest.mark.parametrize("change", ["missing", "true", "number"])
def test_names_the_field_that_changed_shape(client, upstream, field, change):
    build = listed(upstream)[1]
    if change == "missing":
        del build[field]
    else:
        build[field] = {"true": True, "number": 1.5}[change]

    with pytest.raises(UpstreamChangedError) as raised:
        client.builds(BRANCH)

    assert raised.value.operation == "builds"
    assert raised.value.field == f"result[0].builds[1].{field}"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("branch_id", [51044]),
        ("branch_id", ["51044", "feature-invoicing"]),
        ("branch_id", [51044, False]),
        ("start_datetime", "30/09/2026 15:43"),
        ("head_commit_timestamp", "2026-09-30T15:42:10Z"),
        ("head_commit_url", "https://github.com/Acme-Corp/Odoo-Addons/tree/feature-invoicing"),
        ("head_commit_url", "https://github.com/Acme-Corp/Odoo-Addons/commit/3f2a9c1"),
        ("result", True),
        ("status_info", 3),
    ],
)
def test_names_the_field_whose_value_is_not_readable(client, upstream, field, value):
    listed(upstream)[1][field] = value

    with pytest.raises(UpstreamChangedError) as raised:
        client.builds(BRANCH)

    assert raised.value.field == f"result[0].builds[1].{field}"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "result"),
        ({"result": {}}, "result"),
        ({"result": []}, "result"),
        ({"result": [{"builds": []}, {"builds": []}]}, "result"),
        ({"result": [51044]}, "result[0]"),
        ({"result": [{"branch_info": {}}]}, "result[0].builds"),
        ({"result": [{"builds": [88240]}]}, "result[0].builds[0]"),
    ],
)
def test_names_the_part_of_the_answer_that_changed_shape(client, upstream, body, field):
    upstream.bodies[PATH] = body

    with pytest.raises(UpstreamChangedError) as raised:
        client.builds(BRANCH)

    assert raised.value.field == field


def test_a_changed_shape_error_shows_nothing_of_the_answer(client, upstream, session):
    listed(upstream)[1]["head_commit_timestamp"] = "yesterday"

    with pytest.raises(UpstreamChangedError) as raised:
        client.builds(BRANCH)

    assert raised.value.__context__ is None
    text = "".join(traceback.format_exception(raised.value))
    for value in ("Jane Doe", "invoicing", "yesterday", session):
        assert value not in text
