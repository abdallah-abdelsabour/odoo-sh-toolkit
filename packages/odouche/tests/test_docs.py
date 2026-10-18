import copy
import getpass
import json
import re
from pathlib import Path

import httpx2
import pytest

import odouche
from odouche import KEYRING_ENTRY, KEYRING_SERVICE, Secret, _login
from odouche._upstream import logs


ROOT = Path(__file__).parents[3]
GUIDE = ROOT / "docs" / "library.md"
README = ROOT / "packages" / "odouche" / "README.md"
REFERENCE = ROOT / "docs" / "reference.md"
BLOCK = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)
ENTRY = re.compile(r"^::: (\S+)$", re.MULTILINE)
BUILDS = "/app/branch/51044/builds"
LOG = b"Installing modules\nModules loaded\n"
# What the guide is to show, each by a call only its example makes.
SHOWN = [
    "odouche.login(",
    "odouche.logout(",
    ".projects()",
    ".branches(",
    ".builds(",
    ".watch_build(",
    ".read_log(",
    ".follow_log(",
    ".rebuild(",
    ".allow_ip(",
    "except odouche.OdoucheError",
]


def blocks(path):
    """Return the Python examples of a page, each with the line it starts at."""
    text = path.read_text(encoding="utf-8")
    return [(text.count("\n", 0, match.start(1)) + 1, match.group(1)) for match in BLOCK.finditer(text)]


EXAMPLES = [
    pytest.param(path, line, source, id=f"{path.name}:{line}")
    for path in (GUIDE, README)
    for line, source in blocks(path)
]


def log(request):
    """Answer a range of a log as a worker does."""
    asked = request.headers["Range"]
    first = max(len(LOG) - int(asked[7:]), 0) if asked.startswith("bytes=-") else int(asked[6:-1])
    headers = {"Content-Range": f"bytes {first}-{len(LOG) - 1}/{len(LOG)}"}
    return httpx2.Response(206, headers=headers, content=LOG[first:])


@pytest.fixture
def odoo_sh(upstream, backend, session, monkeypatch):
    """Stand in for Odoo.sh, the keyring, the browser and the time, for an example to run as written."""
    listed = upstream.bodies[BUILDS]["result"][0]["builds"]
    # A finished build on a worker: a watch of it ends, and it has logs.
    listed[0].update(listed[1] | {"id": listed[0]["id"]})
    upstream.bodies[f"/paas/build/{listed[0]['id']}/logs/list"] = upstream.load("build_logs_list.json")
    for name in ("install", "odoo"):
        upstream.bodies[f"/paas/build/{listed[0]['id']}/logs/{name}"] = log

    def rebuild(_):
        listed.insert(0, copy.deepcopy(listed[0]) | {"id": listed[0]["id"] + 1})
        return httpx2.Response(200, json=upstream.load("rebuild.json"))

    upstream.bodies["/app/branch/51044/rebuild"] = rebuild
    settings = upstream.load("project_settings.json")

    def set_settings(request):
        stored = json.loads(request.content)["params"]["ip_whitelist"]
        settings["result"]["repository"]["ip_whitelist"] = stored
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ip_whitelist": stored}})

    upstream.bodies["/app/project/acme-corp-odoo-addons-4217/get_settings"] = settings
    upstream.bodies["/app/project/acme-corp-odoo-addons-4217/set_settings"] = set_settings
    now = [0.0]
    monkeypatch.setattr(logs, "_monotonic", lambda: now[0])
    monkeypatch.setattr(logs, "_sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(_login, "_find", lambda: None)
    monkeypatch.setattr(_login, "_transport", upstream.connect)
    monkeypatch.setattr(getpass, "getpass", lambda _: session)
    # As after a login: the session is the stored one.
    odouche.login(ask=lambda: Secret(session))
    upstream.requests.clear()
    return upstream


@pytest.mark.parametrize(("path", "line", "source"), EXAMPLES)
def test_an_example_runs_as_written(path, line, source, odoo_sh, capsys, session):
    # Compiled at its place in the page, so a failure names the line of the example.
    exec(compile("\n" * (line - 1) + source, str(path), "exec"), {"__name__": "__main__"})  # noqa: S102

    assert odoo_sh.requests
    assert session not in capsys.readouterr().out


def test_the_logout_example_deletes_the_stored_session(odoo_sh, backend):
    [source] = [source for _, source in blocks(GUIDE) if "odouche.logout(" in source]

    exec(source, {})  # noqa: S102

    assert (KEYRING_SERVICE, KEYRING_ENTRY) not in backend.entries


def test_the_guide_shows_every_capability():
    sources = [source for _, source in blocks(GUIDE)]

    assert [call for call in SHOWN if not any(call in source for source in sources)] == []


def test_the_reference_has_an_entry_for_every_exported_name_and_no_other():
    entries = ENTRY.findall(REFERENCE.read_text(encoding="utf-8"))

    assert sorted(entries) == sorted(f"odouche.{name}" for name in odouche.__all__)
