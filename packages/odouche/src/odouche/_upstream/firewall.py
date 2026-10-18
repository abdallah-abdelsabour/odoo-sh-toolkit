"""The firewall of a project: the IPv4 addresses Odoo.sh lets reach its production and staging builds.

The settings page keeps them as `ip_whitelist`, one per line: a public address, or a range in CIDR
notation. Saving a setting sends only that setting, and Odoo.sh answers what it stored, or a
`ValidationError` for an entry it does not take, which stores nothing.
"""

import ipaddress

from odouche._upstream import projects
from odouche._upstream.reader import Reader
from odouche._upstream.transport import Transport, state_changing
from odouche.errors import FirewallRefusedError, NotFoundError, OdoucheError, OutcomeUnknownError
from odouche.models import Firewall


_READ = "firewall"
_OPERATION = "firewall change"
_FIELD = "ip_whitelist"
# What the settings page says Odoo.sh takes.
MAX_ADDRESSES = 10


def firewall(transport: Transport, project: str) -> Firewall:
    """Return the firewall of the project named `project`."""
    return _read(transport, project, _technical_name(transport, project, _READ))


def plan(current: Firewall, value: str, *, replace: bool) -> Firewall:
    """Return the firewall that allowing `value` leaves: it alone, or added to the others. Nothing is asked.

    Raises `ValueError` when `value` is not a public IPv4 address or range, and
    `FirewallRefusedError` when the firewall would hold more addresses than Odoo.sh takes.
    """
    added = address(value)
    kept = () if replace else current.addresses
    addresses = kept if added in kept else (*kept, added)
    if len(addresses) > MAX_ADDRESSES:
        raise FirewallRefusedError(
            f"The firewall of {current.project} holds {len(kept)} addresses, and Odoo.sh takes "
            f"{MAX_ADDRESSES} at most. Replace them instead of adding one.",
            operation=_OPERATION,
        )
    return Firewall(current.project, addresses)


@state_changing
def allow(transport: Transport, project: str, value: str, *, replace: bool) -> Firewall:
    """Allow `value` through the firewall of the project named `project`, and return the firewall.

    When the firewall already is what `plan` gives, nothing is sent.
    """
    technical_name = _technical_name(transport, project, _OPERATION)
    current = _read(transport, project, technical_name)
    wanted = plan(current, value, replace=replace)
    if wanted == current:
        return current
    refused = FirewallRefusedError(
        f"Odoo.sh refused the firewall of {project}: it takes public IPv4 addresses and ranges only. "
        "Nothing was changed.",
        operation=_OPERATION,
    )
    answer = transport.change(
        _OPERATION,
        f"/app/project/{technical_name}/set_settings",
        {_FIELD: "\n".join(wanted.addresses)},
        refused=refused,
    )
    try:
        stored = Firewall(project, _addresses(Reader(_OPERATION, answer).child("result")))
    except OdoucheError:
        stored = None
    # Raised as unknown: an error that reads as a failure invites sending it again.
    if stored is None or stored.addresses != wanted.addresses:
        raise OutcomeUnknownError(
            f"The firewall change of {project} was sent, and Odoo.sh did not answer the addresses it was "
            "given. Look before trying again.",
            operation=_OPERATION,
        )
    return stored


def address(value: str) -> str:
    """Return `value` as an entry of the firewall: a public IPv4 address, or a range in CIDR notation.

    An address is written as one is (`8.8.8.8`), a range as its network (`8.8.4.0/24`). Raises
    `ValueError` for anything else, a private or reserved address or range included.
    """
    if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = "An address is a string."
        raise TypeError(msg)
    text = value.strip()
    try:
        # Strict: a range with host bits set, such as 8.8.4.4/24, is not one.
        network = ipaddress.IPv4Network(text)
    except ValueError:
        msg = f"{value!r} is not an IPv4 address or range."
        raise ValueError(msg) from None
    if not network.is_global:
        msg = f"{value!r} is not public: Odoo.sh takes public IPv4 addresses and ranges only."
        raise ValueError(msg)
    return str(network) if "/" in text else str(network.network_address)


def _read(transport: Transport, project: str, technical_name: str) -> Firewall:
    path = f"/app/project/{technical_name}/get_settings"
    answer = Reader(_READ, transport.call(_READ, path, retry=True))
    return Firewall(project, _addresses(answer.child("result").child("repository")))


def _technical_name(transport: Transport, project: str, operation: str) -> str:
    """Return the project's technical name, quoted for the address of a request."""
    technical_name = projects.technical_name(transport, project)
    if technical_name is None:
        raise NotFoundError(f"The session's user can reach no project named {project!r}.", operation=operation)
    return projects.segment(technical_name)


def _addresses(settings: Reader) -> tuple[str, ...]:
    """Read the addresses, one per line. An empty firewall is answered as `false` or as an empty string."""
    text = settings.optional_text(_FIELD) or ""
    return tuple(line.strip() for line in text.splitlines() if line.strip())
