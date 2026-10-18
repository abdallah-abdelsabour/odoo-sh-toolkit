import io
import json
import urllib.request
from typing import Self

import pytest
from typer.testing import CliRunner, Result

import odouche
from odouche import Firewall
from odouche_cli import _confirm, firewall
from odouche_cli.app import app


ACME = odouche.Project(id=1, name="acme", repository="acme/odoo", url="https://www.odoo.sh/project/acme")
KEPT = ("1.1.1.1", "8.8.8.8")
MINE = "9.9.9.9"
ON_ACME = ("--project", "acme")
ALLOW = ("firewall", "allow")
# The lookup itself, which a fixture replaces.
PUBLIC_ADDRESS = firewall.public_address

runner = CliRunner()


class StubClient:
    """Stands in for `odouche.Client`: keeps a firewall and changes it as the library does."""

    addresses: tuple[str, ...] = KEPT
    lost: odouche.OdoucheError | None = None
    """What a change raises once it is sent."""
    modes: list[bool]
    """The `read_only` each client was built with."""
    calls: list[str]

    def __init__(self, *, read_only: bool = False) -> None:
        self.modes.append(read_only)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def projects(self) -> list[odouche.Project]:
        return [ACME]

    def firewall(self, project: str) -> Firewall:
        self.calls.append(f"firewall {project}")
        return Firewall(project, type(self).addresses)

    # The library's own, which asks nothing.
    plan_allow_ip = odouche.Client.plan_allow_ip

    def allow_ip(self, project: str, address: str, *, replace: bool = True) -> Firewall:
        self.calls.append(f"allow {project} {address} replace={replace}")
        if self.lost is not None:
            raise self.lost
        changed = self.plan_allow_ip(Firewall(project, type(self).addresses), address, replace=replace)
        type(self).addresses = changed.addresses
        return changed


@pytest.fixture(autouse=True)
def read_only_clients() -> None:
    """Replace the shared check: `osh firewall allow` writes, and the tests of `list` check it themselves."""


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    class Client(StubClient):
        modes: list[bool] = []
        calls: list[str] = []

    monkeypatch.setattr(odouche, "Client", Client)
    monkeypatch.setattr(_confirm, "stdin_is_terminal", lambda: True)
    monkeypatch.setattr(_confirm, "stderr_is_terminal", lambda: True)

    def unasked() -> str:
        msg = "The public address was looked up."
        raise AssertionError(msg)

    monkeypatch.setattr(firewall, "public_address", unasked)
    return Client


def run(*args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=stdin)


def test_list_shows_the_addresses_read_only(client):
    result = run("firewall", "list", *ON_ACME)

    assert result.exit_code == 0, result.output
    assert "acme" in result.stdout
    assert "1.1.1.1, 8.8.8.8" in result.stdout
    assert client.modes == [True]
    assert client.calls == ["firewall acme"]


def test_list_prints_the_firewall_as_json(client):
    result = run("--format", "json", "firewall", "list", *ON_ACME)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"project": "acme", "addresses": list(KEPT)}


def test_list_says_when_there_is_no_address(client):
    client.addresses = ()

    assert "no address" in run("firewall", "list", *ON_ACME).stdout


def test_allow_replaces_the_addresses_after_asking(client):
    result = run(*ALLOW, MINE, *ON_ACME, stdin="y\n")

    assert result.exit_code == 0, result.output
    assert client.calls[-1] == f"allow acme {MINE} replace=True"
    assert client.modes == [False]
    assert "Now: 1.1.1.1, 8.8.8.8" in result.stderr
    assert f"After: {MINE}" in result.stderr
    assert "Change the firewall? [y/N]" in result.stderr
    assert "up to 5 minutes" in result.stderr
    assert MINE in result.stdout
    assert "Change the firewall" not in result.stdout


def test_allow_add_keeps_the_others(client):
    result = run(*ALLOW, MINE, *ON_ACME, "--add", stdin="yes\n")

    assert result.exit_code == 0, result.output
    assert client.calls[-1] == f"allow acme {MINE} replace=False"
    assert f"After: 1.1.1.1, 8.8.8.8, {MINE}" in result.stderr
    assert client.addresses == (*KEPT, MINE)


@pytest.mark.parametrize("answer", ["n\n", "\n", "nope\n", ""])
def test_anything_but_yes_sends_nothing(client, answer):
    result = run(*ALLOW, MINE, *ON_ACME, stdin=answer)

    assert result.exit_code == 1
    assert "Nothing was sent." in result.stderr
    assert not any(call.startswith("allow") for call in client.calls)


def test_yes_does_not_ask(client):
    result = run(*ALLOW, MINE, *ON_ACME, "--yes")

    assert result.exit_code == 0, result.output
    assert "[y/N]" not in result.stderr
    assert client.calls[-1] == f"allow acme {MINE} replace=True"


@pytest.mark.parametrize("stream", ["stdin_is_terminal", "stderr_is_terminal"])
def test_without_a_terminal_it_needs_yes_and_builds_no_client(client, monkeypatch, stream):
    monkeypatch.setattr(_confirm, stream, lambda: False)

    result = run(*ALLOW, MINE, *ON_ACME)

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert client.modes == []


def test_the_public_address_is_the_default(client, monkeypatch):
    monkeypatch.setattr(firewall, "public_address", lambda: MINE)

    result = run(*ALLOW, *ON_ACME, "--yes")

    assert result.exit_code == 0, result.output
    assert f"Your public address: {MINE}" in result.stderr
    assert client.calls[-1] == f"allow acme {MINE} replace=True"


def test_a_public_address_not_found_exits_15_and_sends_nothing(client, monkeypatch):
    monkeypatch.setattr(firewall, "public_address", lambda: None)

    result = run(*ALLOW, *ON_ACME, "--yes")

    assert result.exit_code == 15
    assert "Give it as ADDRESS" in result.stderr
    assert client.modes == []


@pytest.mark.parametrize("address", ["192.0.2", "2001:db8::1", "example.com"])
def test_an_address_that_is_not_ipv4_is_a_usage_error(client, address):
    result = run(*ALLOW, address, *ON_ACME, "--yes")

    assert result.exit_code == 2
    assert "is not an IPv4 address" in result.stderr
    assert not any(call.startswith("allow") for call in client.calls)


@pytest.mark.parametrize(("address", "flags"), [(KEPT[0], ("--add",)), (MINE, ())])
def test_a_firewall_that_already_is_so_is_not_changed(client, address, flags):
    if not flags:
        client.addresses = (MINE,)

    result = run(*ALLOW, address, *ON_ACME, *flags)

    assert result.exit_code == 0, result.output
    assert "Nothing was sent." in result.stderr
    assert "[y/N]" not in result.stderr
    assert not any(call.startswith("allow") for call in client.calls)


def test_a_private_address_is_a_usage_error(client):
    result = run(*ALLOW, "192.168.1.20", *ON_ACME, "--yes")

    assert result.exit_code == 2
    assert "is not public" in result.stderr
    assert not any(call.startswith("allow") for call in client.calls)


def test_a_range_is_allowed(client):
    result = run(*ALLOW, "8.8.4.0/24", *ON_ACME, "--yes")

    assert result.exit_code == 0, result.output
    assert client.calls[-1] == "allow acme 8.8.4.0/24 replace=True"


def test_an_entry_odoo_sh_refuses_exits_9(client):
    client.lost = odouche.FirewallRefusedError("Odoo.sh refused it. Nothing was changed.", operation="firewall change")

    result = run(*ALLOW, MINE, *ON_ACME, "--yes")

    assert result.exit_code == 9
    assert "Nothing was changed." in result.stderr


def test_adding_to_a_full_firewall_exits_9(client):
    client.addresses = tuple(f"1.1.1.{number}" for number in range(1, 11))

    result = run(*ALLOW, MINE, *ON_ACME, "--add", "--yes")

    assert result.exit_code == 9
    assert "holds 10 addresses" in result.stderr


def test_an_unconfirmed_change_exits_10_and_points_at_the_firewall(client):
    client.lost = odouche.OutcomeUnknownError("It was sent. Look before trying again.", operation="firewall change")

    result = run(*ALLOW, MINE, *ON_ACME, "--yes")

    assert result.exit_code == 10
    assert "`osh firewall list` shows the addresses Odoo.sh has." in result.stderr
    assert "osh builds list" not in result.stderr


class Answer(io.BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.mark.parametrize(
    ("body", "found"), [(b"9.9.9.9\n", MINE), (b"2001:db8::1", None), (b"<html>", None), (b"\xff", None)]
)
def test_the_public_address_is_an_ipv4_address_or_nothing(monkeypatch, body, found):
    asked = []

    def urlopen(url: str, *, timeout: float) -> Answer:
        asked.append((url, timeout))
        return Answer(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert PUBLIC_ADDRESS() == found
    assert asked == [(firewall.LOOKUP_URL, 10.0)]


def test_an_unreachable_lookup_finds_nothing(monkeypatch):
    def urlopen(url: str, *, timeout: float) -> Answer:
        raise OSError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert PUBLIC_ADDRESS() is None
