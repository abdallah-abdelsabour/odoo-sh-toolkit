import dataclasses

import pytest

from odouche import NotFoundError, SshTarget, UpstreamChangedError


BRANCH = 51044


@pytest.fixture
def build(client):
    return client.build(BRANCH, 88212)


def test_the_target_is_the_number_of_the_build_at_the_host_of_its_address(client, upstream, build):
    upstream.requests.clear()

    assert client.ssh_target(build) == SshTarget(user="88212", host="acme-shop-feature-invoicing-88212.dev.odoo.com")
    assert upstream.requests == []


@pytest.mark.parametrize(
    "host",
    [
        "acme-shop.odoo.com",
        "acme-shop-staging-88300.dev.odoo.com",
        "acme--shop-9.dev.odoo.com",
        "4217-shop.odoo.com",
        "a.b.c.odoo.com",
    ],
)
def test_a_host_under_odoo_com_is_a_target(client, build, host):
    assert client.ssh_target(dataclasses.replace(build, url=f"https://{host}")).host == host


def test_a_build_with_no_address_has_no_target(client, build):
    with pytest.raises(NotFoundError, match="Build 88212 has no host"):
        client.ssh_target(dataclasses.replace(build, url=None))


@pytest.mark.parametrize(
    "url",
    [
        "https://-oProxyCommand=id.dev.odoo.com",
        "https://-v.odoo.com",
        "https://acme.-v.odoo.com",
        "https://acme-.odoo.com",
        "-oProxyCommand=id",
        "http://acme-shop.odoo.com",
        "https://acme-shop.odoo.com:22",
        "https://acme-shop.odoo.com/",
        "https://acme-shop.odoo.com/x",
        "https://user@acme-shop.odoo.com",
        "https://acme-shop.odoo.com.example.com",
        "https://example.com",
        "https://odoo.com",
        "https://acme..odoo.com",
        "https://acme-shop.odoo.com?x=1",
        "https://acme-shop.odoo.com#x",
        "https://ACME-shop.odoo.com",
        "https://example.com\\.odoo.com",
        "https://acme-shop.odoo.com\n",
        "https://acme shop.odoo.com",
        "acme-shop.odoo.com",
        "",
        7,
    ],
)
def test_an_address_that_is_not_a_host_of_odoo_com_is_a_changed_upstream(client, build, url):
    with pytest.raises(UpstreamChangedError) as raised:
        client.ssh_target(dataclasses.replace(build, url=url))

    assert raised.value.field == "url"


@pytest.mark.parametrize("number", [-88212, 0, True, "88212", "-oProxyCommand=id", 88212.0, None])
def test_a_number_that_is_not_one_is_a_changed_upstream(client, build, number):
    with pytest.raises(UpstreamChangedError) as raised:
        client.ssh_target(dataclasses.replace(build, id=number))

    assert raised.value.field == "id"
