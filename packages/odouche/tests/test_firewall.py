import json

import httpx2
import pytest

from odouche import (
    Client,
    Firewall,
    FirewallRefusedError,
    NotFoundError,
    OutcomeUnknownError,
    ReadOnlyError,
    Secret,
    SessionExpiredError,
)


PROJECT = "acme-shop"
SETTINGS = "/app/project/acme-corp-odoo-addons-4217/get_settings"
SET = "/app/project/acme-corp-odoo-addons-4217/set_settings"
KEPT = ("1.1.1.1", "8.8.8.8")
MINE = "9.9.9.9"


def paths(upstream):
    return [request.url.path for request in upstream.requests]


def stored(upstream):
    return upstream.bodies[SETTINGS]["result"]["repository"]


def sent(upstream):
    return [json.loads(request.content) for request in upstream.requests if request.url.path == SET]


@pytest.fixture(autouse=True)
def settings(upstream):
    """Answer the settings as Odoo.sh does: a change is stored, and answered as stored."""
    upstream.bodies[SETTINGS] = upstream.load("project_settings.json")

    def answer(request):
        value = json.loads(request.content)["params"]["ip_whitelist"]
        stored(upstream)["ip_whitelist"] = value
        body = upstream.load("set_settings.json")
        body["result"]["ip_whitelist"] = value
        return httpx2.Response(200, json=body)

    upstream.bodies[SET] = answer


def test_reads_the_addresses_one_per_line(client, upstream, session):
    assert client.firewall(PROJECT) == Firewall(PROJECT, KEPT)
    assert paths(upstream) == ["/app/projects", SETTINGS]
    request = upstream.requests[1]
    assert json.loads(request.content) == {"jsonrpc": "2.0", "method": "call", "params": {}, "id": 1}
    assert request.headers["Cookie"] == f"session_id={session}"


@pytest.mark.parametrize("value", [False, None, "", "\n \n"])
def test_an_empty_firewall_has_no_address(client, upstream, value):
    stored(upstream)["ip_whitelist"] = value

    assert client.firewall(PROJECT).addresses == ()


def test_blank_lines_and_spaces_are_not_addresses(client, upstream):
    stored(upstream)["ip_whitelist"] = " 1.1.1.1 \r\n\n8.8.8.8\n"

    assert client.firewall(PROJECT).addresses == KEPT


def test_allowing_replaces_the_addresses_by_default(client, upstream):
    firewall = client.allow_ip(PROJECT, MINE)

    assert firewall == Firewall(PROJECT, (MINE,))
    assert paths(upstream) == ["/app/projects", SETTINGS, SET]
    assert sent(upstream) == [{"jsonrpc": "2.0", "method": "call", "params": {"ip_whitelist": MINE}, "id": 1}]
    assert client.firewall(PROJECT) == firewall


def test_allowing_without_replacing_adds_the_address_last(client, upstream):
    firewall = client.allow_ip(PROJECT, MINE, replace=False)

    assert firewall.addresses == (*KEPT, MINE)
    assert sent(upstream)[0]["params"] == {"ip_whitelist": "\n".join((*KEPT, MINE))}


@pytest.mark.parametrize(("address", "replace"), [(KEPT[1], False), (KEPT[0], False)])
def test_an_address_already_allowed_is_not_sent_again(client, upstream, address, replace):
    assert client.allow_ip(PROJECT, address, replace=replace) == Firewall(PROJECT, KEPT)
    assert sent(upstream) == []


def test_replacing_with_the_only_address_sends_nothing(client, upstream):
    stored(upstream)["ip_whitelist"] = MINE

    assert client.allow_ip(PROJECT, MINE) == Firewall(PROJECT, (MINE,))
    assert sent(upstream) == []


def test_an_address_is_written_as_odoo_sh_keeps_it(client, upstream):
    assert client.allow_ip(PROJECT, f"  {MINE}\n").addresses == (MINE,)
    assert sent(upstream)[0]["params"] == {"ip_whitelist": MINE}


@pytest.mark.parametrize(
    "address", ["", "192.0.2", "192.0.2.256", "2001:db8::1", "8.8.4.4/24", "8.8.4.0/33", "9.9.9.9\n1.1.1.1"]
)
def test_anything_but_an_ipv4_address_or_range_is_refused_before_sending(client, upstream, address):
    with pytest.raises(ValueError, match="is not an IPv4 address or range"):
        client.allow_ip(PROJECT, address)
    assert sent(upstream) == []


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.20", "127.0.0.1", "100.64.0.1", "192.0.2.1", "10.0.0.0/8"])
def test_a_private_or_reserved_address_is_refused_before_sending(client, upstream, address):
    with pytest.raises(ValueError, match="is not public"):
        client.allow_ip(PROJECT, address)
    assert sent(upstream) == []


@pytest.mark.parametrize(("given", "kept"), [("8.8.4.0/24", "8.8.4.0/24"), (" 9.9.9.9/32 ", "9.9.9.9/32")])
def test_a_range_is_kept_in_cidr_notation(client, upstream, given, kept):
    assert client.allow_ip(PROJECT, given).addresses == (kept,)
    assert sent(upstream)[0]["params"] == {"ip_whitelist": kept}


@pytest.mark.parametrize("name", ["odoo.exceptions.ValidationError", "odoo.exceptions.UserError"])
def test_an_entry_odoo_sh_does_not_take_is_refused_and_changes_nothing(client, upstream, name):
    upstream.bodies[SET] = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": 0, "message": "Odoo Server Error", "data": {"name": name, "message": "Not public."}},
    }

    with pytest.raises(FirewallRefusedError, match="Nothing was changed") as raised:
        client.allow_ip(PROJECT, MINE)
    assert raised.value.operation == "firewall change"
    assert "Not public." not in str(raised.value)
    assert len(sent(upstream)) == 1
    assert client.firewall(PROJECT).addresses == KEPT


def test_an_address_that_is_not_a_string_is_refused(client, upstream):
    with pytest.raises(TypeError):
        client.allow_ip(PROJECT, 3221225991)  # pyright: ignore[reportArgumentType]
    assert sent(upstream) == []


def test_adding_past_ten_addresses_is_refused_and_replacing_is_not(client, upstream):
    ten = [f"1.1.1.{number}" for number in range(1, 11)]
    stored(upstream)["ip_whitelist"] = "\n".join(ten)

    with pytest.raises(FirewallRefusedError, match="holds 10 addresses"):
        client.allow_ip(PROJECT, MINE, replace=False)
    assert sent(upstream) == []
    assert client.allow_ip(PROJECT, ten[3], replace=False).addresses == tuple(ten)
    assert client.allow_ip(PROJECT, MINE).addresses == (MINE,)


def test_planning_asks_odoo_sh_nothing(client, upstream):
    current = Firewall(PROJECT, KEPT)

    assert client.plan_allow_ip(current, MINE) == Firewall(PROJECT, (MINE,))
    assert client.plan_allow_ip(current, MINE, replace=False) == Firewall(PROJECT, (*KEPT, MINE))
    assert upstream.requests == []


def test_a_read_only_client_sends_nothing(upstream, session):
    client = Client(Secret(session), read_only=True)

    with pytest.raises(ReadOnlyError):
        client.allow_ip(PROJECT, MINE)
    assert upstream.requests == []
    assert client.firewall(PROJECT).addresses == KEPT


@pytest.mark.parametrize(
    "answer",
    [
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "result": True},
        {"jsonrpc": "2.0", "id": 1, "result": {"ip_whitelist": KEPT[0]}},
    ],
)
def test_an_answer_without_the_addresses_sent_is_an_unknown_outcome(client, upstream, answer):
    upstream.bodies[SET] = answer

    with pytest.raises(OutcomeUnknownError, match="Look before trying again") as raised:
        client.allow_ip(PROJECT, MINE)
    assert raised.value.operation == "firewall change"
    assert len(sent(upstream)) == 1


def test_a_server_error_is_an_unknown_outcome_and_is_not_sent_again(client, upstream):
    upstream.failures[SET] = 502

    with pytest.raises(OutcomeUnknownError):
        client.allow_ip(PROJECT, MINE)
    assert paths(upstream).count(SET) == 1


def test_a_project_out_of_reach_is_not_found(client, upstream):
    with pytest.raises(NotFoundError, match="no project named 'nope'"):
        client.firewall("nope")
    with pytest.raises(NotFoundError):
        client.allow_ip("nope", MINE)
    assert sent(upstream) == []


def test_a_rejected_session_is_reported(client, upstream):
    upstream.bodies[SETTINGS] = upstream.load("unauthenticated.json")

    with pytest.raises(SessionExpiredError):
        client.allow_ip(PROJECT, MINE)
    assert sent(upstream) == []
