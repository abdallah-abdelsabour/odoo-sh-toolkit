"""The projects request."""

from urllib.parse import quote

from odouche._upstream.reader import Reader
from odouche._upstream.transport import Transport
from odouche.models import Project
from odouche.secret import Secret


PATH = "/app/projects"

_OPERATION = "projects"
_INFO = "project"


def projects(transport: Transport) -> list[Project]:
    """List the projects the session's user can reach. Odoo.sh answers them all at once."""
    return [
        Project(
            id=repo.integer("id"),
            name=repo.text("project_name"),
            repository=f"{repo.text('owner')}/{repo.text('name')}",
            url=repo.text("project_url"),
        )
        for repo in _repos(transport)
    ]


def technical_name(transport: Transport, name: str) -> str | None:
    """Return the name a project's other requests address it by, or `None` when it is not listed."""
    for repo in _repos(transport):
        if repo.text("project_name") == name:
            return repo.text("technical_name")
    return None


def project_id(transport: Transport, name: str, *, within: float | None = None) -> int | None:
    """Return the number of the project named `name`, or `None` when it is not listed."""
    for repo in _repos(transport, within):
        if repo.text("project_name") == name:
            return repo.integer("id")
    return None


def segment(name: str) -> str:
    """Quote a name for the address of a request. A dot is quoted too: the client resolves `..`."""
    return quote(name, safe="").replace(".", "%2E")


def access_token(transport: Transport, name: str) -> Secret:
    """Return the token the workers of the project named `name` take. It opens every build's logs."""
    answer = Reader(
        _INFO,
        transport.call(
            _INFO,
            f"/app/project/{segment(name)}/get_info",
            retry=True,
            not_found=f"The session's user can reach no project named {name!r}.",
        ),
    )
    result = answer.child("result")
    token = result.text("access_token")
    if not token:
        raise result.changed("access_token")
    return Secret(token)


def _repos(transport: Transport, within: float | None = None) -> list[Reader]:
    answer = Reader(_OPERATION, transport.call(_OPERATION, PATH, retry=True, within=within))
    return answer.child("result").items("repos")
