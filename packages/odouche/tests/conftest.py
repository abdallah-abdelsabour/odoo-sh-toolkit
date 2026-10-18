import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError, PasswordSetError

from odouche import SESSION_ENV, Client, Secret, _client, _logout, _session
from odouche._upstream.transport import Transport


FIXTURES = Path(__file__).parent / "fixtures"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, tzinfo=UTC)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def _memory():
    class Memory(KeyringBackend):
        """Keeps entries on the class: the store builds a backend on every use."""

        priority = 1  # pyright: ignore[reportAssignmentType]
        entries = {}
        calls = 0
        failure = None
        refuses_deletion = False
        failing_writes = 0
        write_failure = PasswordSetError
        dialog = None

        @classmethod
        def lock(cls):
            """Show a dialog: every call blocks until `unlock`."""
            cls.dialog = threading.Event()

        @classmethod
        def unlock(cls):
            """Answer the dialog and let the calls it blocked end."""
            if cls.dialog is not None:
                cls.dialog.set()
            for thread in threading.enumerate():
                if thread.name == _session._WORKER:
                    thread.join()

        def _call(self):
            if self.dialog is not None:
                self.dialog.wait()
            type(self).calls += 1
            if self.failure is not None:
                raise self.failure(f"failed, entry: {self.entries}")

        def get_password(self, service, username):
            self._call()
            return self.entries.get((service, username))

        def set_password(self, service, username, password):
            self._call()
            if self.failing_writes:
                type(self).failing_writes -= 1
                raise self.write_failure(f"failed, entry: {password}")
            # As Credential Locker does with the entry it overwrites.
            if (service, username) in self.entries:
                self.entries[f"{username}@{service}", username] = self.entries[service, username]
            self.entries[service, username] = password

        def delete_password(self, service, username):
            self._call()
            if self.refuses_deletion or (service, username) not in self.entries:
                raise PasswordDeleteError("No such password!")
            del self.entries[service, username]

    return Memory


@pytest.fixture
def backend(monkeypatch):
    backend = _memory()
    monkeypatch.setattr(_session, "_accepted", lambda: backend)
    return backend


@pytest.fixture
def no_backend(monkeypatch):
    class NotViable(_memory()):
        viable = False  # pyright: ignore[reportAssignmentType]

    monkeypatch.setattr(_session, "_accepted", lambda: NotViable)
    return NotViable


@pytest.fixture
def memory():
    return _memory


LOGOUT = "/web/session/logout"


class Upstream:
    """Answers each request with the body of its path, and keeps the requests and the transports it was reached through.

    The logout is answered as Odoo.sh does. A path in `failures` answers that status, or raises
    that error. A body that is a function answers the request itself.
    """

    def __init__(self):
        self.bodies = {
            "/app/projects": self.load("projects.json"),
            "/app/project/acme-corp-odoo-addons-4217/branches": self.load("branches.json"),
            "/app/branch/51044/builds": self.load("builds.json"),
            "/app/user/profile": self.load("user_profile.json"),
            "/app/project/acme-shop/get_info": self.load("project_info.json"),
            "/paas/build/88212/logs/list": self.load("build_logs_list.json"),
        }
        self.failures = {}
        self.requests = []
        self.transports = []

    @staticmethod
    def load(name):
        return json.loads((FIXTURES / name).read_text())

    def connect(self, session, **options):
        options.setdefault("sleep", lambda _: None)
        self.transports.append(Transport(session, transport=httpx2.MockTransport(self._answer), **options))
        return self.transports[-1]

    def _answer(self, request):
        self.requests.append(request)
        failure = self.failures.get(request.url.path)
        if failure is KeyboardInterrupt:
            raise failure
        if isinstance(failure, type):
            raise failure(f"failed, Cookie: {request.headers.get('Cookie')}", request=request)
        if failure is not None:
            return httpx2.Response(failure)
        if request.url.path == LOGOUT:
            return httpx2.Response(303, headers={"Location": "/"})
        body = self.bodies[request.url.path]
        if isinstance(body, dict):
            return httpx2.Response(200, json=body)
        return body(request)


@pytest.fixture
def upstream(monkeypatch):
    upstream = Upstream()
    monkeypatch.setattr(_client, "_transport", upstream.connect)
    monkeypatch.setattr(_logout, "_transport", upstream.connect)
    monkeypatch.delenv(SESSION_ENV, raising=False)
    yield upstream
    for transport in upstream.transports:
        transport.close()


@pytest.fixture
def session():
    return "s3ss10n-v4lu3"


@pytest.fixture
def client(upstream, session):
    return Client(Secret(session))
