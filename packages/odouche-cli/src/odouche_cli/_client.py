"""The library client, built in one place and only when a command needs it."""

import odouche


def open_client(*, writes: bool = False) -> odouche.Client:
    """Open the client on the session in use: read-only, unless the command is one that `writes`."""
    return odouche.Client(read_only=not writes)
