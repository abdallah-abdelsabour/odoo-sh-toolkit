"""A wrapper that keeps a sensitive value out of anything printed or serialized."""

import hmac
from typing import NoReturn, SupportsIndex, final


_PLACEHOLDER = "**********"


@final
class Secret:
    """A sensitive string, such as an Odoo.sh session, that cannot print itself.

    `repr()`, `str()` and `format()` return a placeholder, and pickling or copying raises
    `TypeError`. The value leaves only through `expose_secret`.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Secret wraps a str")
        self.__value = value

    def expose_secret(self) -> str:
        """Return the wrapped value."""
        return self.__value

    def __repr__(self) -> str:
        return _PLACEHOLDER

    def __str__(self) -> str:
        return _PLACEHOLDER

    def __format__(self, format_spec: str) -> str:
        return format(_PLACEHOLDER, format_spec)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self.__value.encode(), other.__value.encode())

    # A hash of a secret is a fingerprint of it.
    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Secret cannot be pickled or copied")

    # The default returns the slot values.
    def __getstate__(self) -> NoReturn:
        raise TypeError("Secret cannot be pickled or copied")
