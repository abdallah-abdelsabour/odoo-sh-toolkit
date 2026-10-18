import ast
from importlib.metadata import version
from pathlib import Path

import pytest

import odouche


PACKAGES = Path(__file__).parents[2]


def private_imports(source):
    """Return the modules and names under `odouche` that a source imports and that are private."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            root, *parts = name.split(".")
            if root == "odouche" and any(part.startswith("_") and not part.startswith("__") for part in parts):
                found.append(name)
    return found


def test_version_matches_distribution_metadata():
    assert odouche.__version__ == version("odouche")


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import odouche\nfrom odouche import Client, __version__\nfrom odouche.errors import OdoucheError", []),
        ("import odouche._session", ["odouche._session"]),
        ("from odouche import _client", ["odouche._client"]),
        ("from odouche._upstream.transport import Transport", ["odouche._upstream.transport.Transport"]),
        ("import odouche_cli._private\nfrom . import _private", []),
    ],
)
def test_finds_an_import_of_a_private_module(source, found):
    assert private_imports(source) == found


@pytest.mark.parametrize("frontend", ["odouche-cli", "odouche-mcp"])
def test_a_frontend_imports_only_the_public_api(frontend):
    sources = sorted((PACKAGES / frontend / "src").rglob("*.py"))

    assert sources
    assert [name for path in sources for name in private_imports(path.read_text(encoding="utf-8"))] == []
