import copy
import logging
import pickle
from dataclasses import dataclass

import pytest

import odouche
from odouche import Secret


VALUE = "s3ss10n-v4lu3"
PLACEHOLDER = "**********"


@pytest.fixture
def wrapped():
    return Secret(VALUE)


def test_is_exported():
    assert "Secret" in odouche.__all__


def test_expose_secret_returns_the_value(wrapped):
    assert wrapped.expose_secret() == VALUE


def test_is_not_a_str(wrapped):
    assert not isinstance(wrapped, str)


@pytest.mark.parametrize("value", [None, b"bytes", 42, Secret(VALUE)])
def test_rejects_anything_but_a_str(value):
    with pytest.raises(TypeError) as raised:
        Secret(value)

    assert VALUE not in str(raised.value)


@pytest.mark.parametrize(
    "render",
    [
        repr,
        str,
        format,
        lambda wrapped: f"{wrapped}",
        lambda wrapped: f"{wrapped!r}",
        lambda wrapped: f"{wrapped:>20}",
        lambda wrapped: "%s %r" % (wrapped, wrapped),  # noqa: UP031
        "{}".format,
    ],
)
def test_renders_as_the_placeholder(wrapped, render):
    rendered = render(wrapped)

    assert VALUE not in rendered
    assert PLACEHOLDER in rendered


def test_logging_shows_the_placeholder(wrapped, caplog):
    with caplog.at_level(logging.INFO):
        logging.getLogger("odouche.test").info("session %s %r", wrapped, wrapped)

    assert caplog.messages == [f"session {PLACEHOLDER} {PLACEHOLDER}"]
    assert VALUE not in caplog.text


def test_dataclass_repr_shows_the_placeholder(wrapped):
    @dataclass(frozen=True)
    class Holder:
        name: str
        session: Secret

    assert repr(Holder("dev", wrapped)) == f"{Holder.__qualname__}(name='dev', session={PLACEHOLDER})"


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_cannot_be_pickled(wrapped, protocol):
    with pytest.raises(TypeError):
        pickle.dumps(wrapped, protocol)


@pytest.mark.parametrize("duplicate", [copy.copy, copy.deepcopy])
def test_cannot_be_copied(wrapped, duplicate):
    with pytest.raises(TypeError):
        duplicate(wrapped)


def test_has_no_state_to_read(wrapped):
    with pytest.raises(TypeError):
        vars(wrapped)
    with pytest.raises(TypeError):
        wrapped.__getstate__()


def test_equality_compares_the_values(wrapped):
    assert wrapped == Secret(VALUE)
    assert wrapped != Secret("another")
    assert wrapped != VALUE


def test_cannot_be_hashed(wrapped):
    with pytest.raises(TypeError):
        hash(wrapped)
