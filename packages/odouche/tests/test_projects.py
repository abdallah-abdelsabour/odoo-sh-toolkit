import dataclasses
import traceback

import pytest

from odouche import (
    SESSION_ENV,
    Client,
    NoSessionError,
    Project,
    Secret,
    SessionExpiredError,
    UpstreamChangedError,
)
from odouche._session import KEYRING_ENTRY, KEYRING_SERVICE, SessionStore


KEY = (KEYRING_SERVICE, KEYRING_ENTRY)
PATH = "/app/projects"


def test_lists_the_projects_as_models(client, upstream, session):
    assert client.projects() == [
        Project(
            id=4217,
            name="acme-shop",
            repository="Acme-Corp/Odoo-Addons",
            url="https://www.odoo.sh/project/acme-shop",
        ),
        Project(
            id=4302,
            name="intranet",
            repository="acme-corp/intranet-addons",
            url="https://www.odoo.sh/project/intranet",
        ),
    ]
    (request,) = upstream.requests
    assert request.url.path == PATH
    assert request.headers["Cookie"] == f"session_id={session}"


def test_a_project_is_immutable_and_holds_no_payload(client):
    project = client.projects()[0]

    assert [field.name for field in dataclasses.fields(project)] == ["id", "name", "repository", "url"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        project.name = "other"


def test_no_project_is_an_empty_list(client, upstream):
    upstream.bodies[PATH]["result"]["repos"] = []

    assert client.projects() == []


def test_asks_again_on_every_call(client, upstream):
    client.projects()
    upstream.bodies[PATH]["result"]["repos"].pop()

    assert len(client.projects()) == 1
    assert len(upstream.requests) == 2


def test_ignores_a_field_it_does_not_read(client, upstream):
    expected = client.projects()
    upstream.bodies[PATH]["result"]["added"] = {"later": True}
    for repo in upstream.bodies[PATH]["result"]["repos"]:
        repo["added"] = "later"

    assert client.projects() == expected


@pytest.mark.parametrize("field", ["id", "project_name", "owner", "name", "project_url"])
@pytest.mark.parametrize("change", ["missing", "false", "true", "list"])
def test_names_the_field_that_changed_shape(client, upstream, field, change):
    repo = upstream.bodies[PATH]["result"]["repos"][1]
    if change == "missing":
        del repo[field]
    else:
        repo[field] = {"false": False, "true": True, "list": [repo[field]]}[change]

    with pytest.raises(UpstreamChangedError) as raised:
        client.projects()

    assert raised.value.operation == "projects"
    assert raised.value.field == f"result.repos[1].{field}"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "result"),
        ({"result": []}, "result"),
        ({"result": {}}, "result.repos"),
        ({"result": {"repos": {}}}, "result.repos"),
        ({"result": {"repos": ["acme-shop"]}}, "result.repos[0]"),
    ],
)
def test_names_the_part_of_the_answer_that_changed_shape(client, upstream, body, field):
    upstream.bodies[PATH] = body

    with pytest.raises(UpstreamChangedError) as raised:
        client.projects()

    assert raised.value.field == field


def test_a_changed_shape_error_shows_nothing_of_the_answer(client, upstream, session):
    del upstream.bodies[PATH]["result"]["repos"][0]["project_url"]

    with pytest.raises(UpstreamChangedError) as raised:
        client.projects()

    text = "".join(traceback.format_exception(raised.value))
    for value in ("acme-shop", "Acme-Corp", "octo-dev", session):
        assert value not in text


def test_a_rejected_session_that_was_passed_leaves_the_keyring_alone(client, upstream, backend):
    upstream.bodies[PATH] = upstream.load("unauthenticated.json")

    with pytest.raises(SessionExpiredError):
        client.projects()

    assert backend.calls == 0


def test_uses_the_stored_session_and_deletes_it_once_rejected(upstream, backend, session):
    SessionStore().save(Secret(session))
    client = Client()

    assert len(client.projects()) == 2
    assert upstream.requests[0].headers["Cookie"] == f"session_id={session}"

    upstream.bodies[PATH] = upstream.load("unauthenticated.json")
    with pytest.raises(SessionExpiredError):
        client.projects()

    assert KEY not in backend.entries


def test_uses_the_session_in_the_environment(upstream, backend, monkeypatch, session):
    monkeypatch.setenv(SESSION_ENV, session)

    Client().projects()

    assert upstream.requests[0].headers["Cookie"] == f"session_id={session}"
    assert backend.calls == 0


def test_there_is_no_client_without_a_session(upstream, backend):
    with pytest.raises(NoSessionError):
        Client()

    assert upstream.transports == []


def test_closes_its_connections(upstream, session):
    with Client(Secret(session)) as client:
        client.projects()

    (transport,) = upstream.transports
    assert transport._client.is_closed


def test_does_not_show_the_session(client, session):
    assert session not in repr(client)
    assert session not in repr(vars(client))
