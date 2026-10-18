"""The rebuild request, which is the one that changes state on Odoo.sh."""

from odouche._upstream import branches, builds
from odouche._upstream.transport import Transport, state_changing
from odouche.errors import OdoucheError, OutcomeUnknownError, StageRefusedError
from odouche.models import Build, Stage


_OPERATION = "rebuild"
# Seen on a development branch only. Staging is let through unseen.
_REBUILT = frozenset({Stage.DEVELOPMENT, Stage.STAGING})


@state_changing
def rebuild(transport: Transport, branch_id: int, stage: Stage | None = None) -> Build:
    """Start a new build of a branch and return it. `stage` is the one the caller holds, if any."""
    if stage is not None:
        allow(stage)
    before = builds.entry(transport, branch_id, 1)
    # The caller's branch may have changed stage since it was read.
    allow(branches.stage(before.child("branch_info").text("stage")))
    latest = max((build.integer("id") for build in before.items("builds")), default=0)
    transport.change(_OPERATION, f"/app/branch/{branch_id}/rebuild")
    try:
        after = builds.builds(transport, branch_id)
    except OdoucheError:
        after = []
    if not after or after[0].id <= latest:
        # Raised out of the handler, and as unknown: an error that reads as a failure invites a second rebuild.
        raise OutcomeUnknownError(
            f"The rebuild of branch {branch_id} was sent, and its build was not found. Look before trying again.",
            operation=_OPERATION,
        )
    return after[0]


def allow(stage: Stage) -> None:
    """Raise `StageRefusedError` for a stage that is not rebuilt. Nothing is asked."""
    if stage not in _REBUILT:
        raise StageRefusedError(
            f"A branch in the {stage.value} stage is not rebuilt: only a development or a staging one is.",
            operation=_OPERATION,
        )
