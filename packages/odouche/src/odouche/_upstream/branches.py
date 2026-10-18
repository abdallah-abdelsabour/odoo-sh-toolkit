"""The branches request."""

import logging

from odouche._upstream import projects
from odouche._upstream.reader import Reader
from odouche._upstream.transport import Transport
from odouche.errors import NotFoundError
from odouche.models import Branch, Stage


_OPERATION = "branches"
_STAGES = {"production": Stage.PRODUCTION, "staging": Stage.STAGING, "dev": Stage.DEVELOPMENT}

_logger = logging.getLogger("odouche")


def branches(transport: Transport, project: str) -> list[Branch]:
    """List the branches of the project named `project`, in the order Odoo.sh answers them."""
    technical_name = projects.technical_name(transport, project)
    if technical_name is None:
        raise NotFoundError(f"The session's user can reach no project named {project!r}.", operation=_OPERATION)
    path = f"/app/project/{projects.segment(technical_name)}/branches"
    answer = Reader(_OPERATION, transport.call(_OPERATION, path, retry=True))
    return [
        Branch(
            id=branch.integer("id"),
            name=branch.text("name"),
            stage=stage(branch.text("stage")),
            stage_name=branch.text("stage"),
        )
        for branch in answer.items("result")
    ]


def stage(name: str) -> Stage:
    """Return the stage Odoo.sh calls `name`."""
    known = _STAGES.get(name)
    if known is None:
        _logger.debug("Unknown branch stage: %r", name)
        return Stage.UNKNOWN
    return known
