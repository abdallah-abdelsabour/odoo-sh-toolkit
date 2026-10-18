"""The one reader of an answer's fields, which names the field that is not what the reference describes."""

from collections.abc import Mapping
from typing import Self, cast

from odouche.errors import UpstreamChangedError


class Reader:
    """Reads the fields of one object of an answer.

    A field that is missing or of another type raises `UpstreamChangedError` with the field's
    path, such as `result.repos[0].id`, and nothing of the answer. A field nobody reads is ignored.
    """

    def __init__(self, operation: str, fields: Mapping[str, object], at: str = "") -> None:
        self._operation = operation
        self._fields = fields
        self._at = at

    def text(self, key: str) -> str:
        """Return a string field."""
        return self._read(key, str)

    def integer(self, key: str) -> int:
        """Return an integer field. A boolean is not one."""
        return self._read(key, int)

    def optional_text(self, key: str) -> str | None:
        """Return a string field, or `None` when Odoo.sh answers `false` or `null` for an absent value."""
        if key in self._fields and (self._fields[key] is False or self._fields[key] is None):
            return None
        return self._read(key, str)

    def has(self, key: str) -> bool:
        """Tell whether a field is there, whatever it holds."""
        return key in self._fields

    def pair(self, key: str) -> tuple[int, str]:
        """Return a many-to-one field, which is a list of an id and a name."""
        value = self._read(key, list)
        if len(value) != 2 or type(value[0]) is not int or type(value[1]) is not str:  # noqa: PLR2004
            raise UpstreamChangedError(self._operation, self._path(key))
        return value[0], value[1]

    def changed(self, key: str) -> UpstreamChangedError:
        """Return the error for a field whose value is of the right type and still not readable."""
        return UpstreamChangedError(self._operation, self._path(key))

    def child(self, key: str) -> Self:
        """Return the reader of an object field."""
        return self._object(self._path(key), self._fields.get(key))

    def items(self, key: str) -> list[Self]:
        """Return the readers of a field that is a list of objects."""
        at = self._path(key)
        return [self._object(f"{at}[{index}]", item) for index, item in enumerate(self._read(key, list))]

    def _path(self, key: str) -> str:
        return f"{self._at}.{key}" if self._at else key

    def _read[T](self, key: str, kind: type[T]) -> T:
        value = self._fields.get(key)
        if type(value) is not kind:
            raise UpstreamChangedError(self._operation, self._path(key))
        return cast("T", value)

    def _object(self, at: str, value: object) -> Self:
        if not isinstance(value, dict):
            raise UpstreamChangedError(self._operation, at)
        return type(self)(self._operation, cast("dict[str, object]", value), at)
