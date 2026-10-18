import dataclasses
import logging
import traceback

import pytest

from odouche import Branch, NotFoundError, PermissionDeniedError, Stage, UpstreamChangedError


PROJECTS = "/app/projects"
PATH = "/app/project/acme-corp-odoo-addons-4217/branches"


def test_lists_the_branches_with_their_stage_in_the_order_of_the_answer(client, upstream, session):
    assert client.branches("acme-shop") == [
        Branch(id=51001, name="19.0", stage=Stage.PRODUCTION, stage_name="production"),
        Branch(id=51044, name="feature-invoicing", stage=Stage.DEVELOPMENT, stage_name="dev"),
        Branch(id=51020, name="staging", stage=Stage.STAGING, stage_name="staging"),
    ]
    assert [request.url.path for request in upstream.requests] == [PROJECTS, PATH]
    assert upstream.requests[1].headers["Cookie"] == f"session_id={session}"


def test_takes_a_project_or_its_name(client):
    project = client.projects()[0]

    assert client.branches(project) == client.branches(project.name)


def test_a_branch_is_immutable_and_holds_no_payload(client):
    branch = client.branches("acme-shop")[0]

    assert [field.name for field in dataclasses.fields(branch)] == ["id", "name", "stage", "stage_name"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        branch.name = "other"


def test_an_unseen_stage_is_unknown_and_keeps_its_name(client, upstream, caplog):
    upstream.bodies[PATH]["result"][1]["stage"] = "duplicate"

    with caplog.at_level(logging.DEBUG, logger="odouche"):
        branches = client.branches("acme-shop")

    assert [branch.stage for branch in branches] == [Stage.PRODUCTION, Stage.UNKNOWN, Stage.STAGING]
    assert branches[1].stage_name == "duplicate"
    assert "Unknown branch stage: 'duplicate'" in caplog.messages


def test_no_branch_is_an_empty_list(client, upstream):
    upstream.bodies[PATH]["result"] = []

    assert client.branches("acme-shop") == []


@pytest.mark.parametrize("name", ["no-such-project", "ACME-SHOP", "acme-corp-odoo-addons-4217", ""])
def test_a_project_that_is_not_listed_is_not_found(client, upstream, name):
    with pytest.raises(NotFoundError) as raised:
        client.branches(name)

    assert raised.value.operation == "branches"
    assert [request.url.path for request in upstream.requests] == [PROJECTS]


def test_a_project_whose_access_was_lost_is_denied(client, upstream):
    upstream.bodies[PATH] = upstream.load("access_error.json")

    with pytest.raises(PermissionDeniedError) as raised:
        client.branches("acme-shop")

    assert raised.value.operation == "branches"
    assert "octo-dev" not in "".join(traceback.format_exception(raised.value))


@pytest.mark.parametrize(
    ("technical_name", "sent"),
    [
        ("../branch/1/rebuild?x=", "%2E%2E%2Fbranch%2F1%2Frebuild%3Fx%3D"),
        ("..", "%2E%2E"),
        (".", "%2E"),
    ],
)
def test_the_name_odoo_sh_gives_cannot_change_the_address(client, upstream, technical_name, sent):
    upstream.bodies[PROJECTS]["result"]["repos"][0]["technical_name"] = technical_name
    upstream.bodies[f"/app/project/{technical_name}/branches"] = upstream.bodies[PATH]

    assert len(client.branches("acme-shop")) == 3
    assert upstream.requests[1].url.raw_path == f"/app/project/{sent}/branches".encode()


@pytest.mark.parametrize("field", ["id", "name", "stage"])
@pytest.mark.parametrize("change", ["missing", "false", "true", "list"])
def test_names_the_field_that_changed_shape(client, upstream, field, change):
    branch = upstream.bodies[PATH]["result"][2]
    if change == "missing":
        del branch[field]
    else:
        branch[field] = {"false": False, "true": True, "list": [branch[field]]}[change]

    with pytest.raises(UpstreamChangedError) as raised:
        client.branches("acme-shop")

    assert raised.value.operation == "branches"
    assert raised.value.field == f"result[2].{field}"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "result"),
        ({"result": {}}, "result"),
        ({"result": ["19.0"]}, "result[0]"),
    ],
)
def test_names_the_part_of_the_answer_that_changed_shape(client, upstream, body, field):
    upstream.bodies[PATH] = body

    with pytest.raises(UpstreamChangedError) as raised:
        client.branches("acme-shop")

    assert raised.value.field == field


def test_a_project_listed_without_its_address_is_a_changed_shape(client, upstream):
    del upstream.bodies[PROJECTS]["result"]["repos"][0]["technical_name"]

    with pytest.raises(UpstreamChangedError) as raised:
        client.branches("acme-shop")

    assert raised.value.operation == "projects"
    assert raised.value.field == "result.repos[0].technical_name"


def test_a_changed_shape_error_shows_nothing_of_the_answer(client, upstream, session):
    del upstream.bodies[PATH]["result"][1]["stage"]

    with pytest.raises(UpstreamChangedError) as raised:
        client.branches("acme-shop")

    text = "".join(traceback.format_exception(raised.value))
    for value in ("feature-invoicing", "Acme-Corp", session):
        assert value not in text
