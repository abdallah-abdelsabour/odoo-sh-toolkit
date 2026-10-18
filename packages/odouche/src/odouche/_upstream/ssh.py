"""The SSH target of a build, which Odoo.sh gives in no payload: the page shows `<id>@<host of url>`."""

import re

from odouche.errors import NotFoundError, UpstreamChangedError
from odouche.models import Build, SshTarget


_OPERATION = "ssh target"
# No label starts with a dash, which `ssh` would read as an option.
_URL = re.compile(r"https://((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+odoo\.com)")


def target(build: Build) -> SshTarget:
    """Return the user and the host of a build, each checked before it becomes an argument of `ssh`."""
    # A negative number would start with a dash.
    if type(build.id) is not int or build.id < 1:
        raise UpstreamChangedError(_OPERATION, "id")
    if build.url is None:
        raise NotFoundError(f"Build {build.id} has no host to connect to.", operation=_OPERATION)
    found = _URL.fullmatch(build.url) if type(build.url) is str else None
    if found is None:
        raise UpstreamChangedError(_OPERATION, "url")
    return SshTarget(user=str(build.id), host=found.group(1))
