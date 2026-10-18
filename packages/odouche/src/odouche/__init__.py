"""Unofficial Python client for Odoo.sh."""

from importlib.metadata import version

from odouche._client import Client
from odouche._login import LoginStep, login
from odouche._logout import logout
from odouche._session import KEYRING_ENTRY, KEYRING_SERVICE, SESSION_ENV
from odouche.errors import (
    FirewallRefusedError,
    KeyringUnavailableError,
    LoginError,
    LoginTimeoutError,
    NoSessionError,
    NotFoundError,
    OdoucheError,
    OutcomeUnknownError,
    PermissionDeniedError,
    ReadOnlyError,
    SessionExpiredError,
    StageRefusedError,
    StreamTimeoutError,
    UpstreamChangedError,
    UpstreamUnavailableError,
)
from odouche.models import (
    Branch,
    Build,
    BuildResult,
    BuildStatus,
    Commit,
    Firewall,
    Identity,
    Log,
    LogKind,
    LogLine,
    LogoutResult,
    Project,
    SessionInfo,
    SessionSource,
    SshTarget,
    Stage,
)
from odouche.secret import Secret


__all__ = [
    "KEYRING_ENTRY",
    "KEYRING_SERVICE",
    "SESSION_ENV",
    "Branch",
    "Build",
    "BuildResult",
    "BuildStatus",
    "Client",
    "Commit",
    "Firewall",
    "FirewallRefusedError",
    "Identity",
    "KeyringUnavailableError",
    "Log",
    "LogKind",
    "LogLine",
    "LoginError",
    "LoginStep",
    "LoginTimeoutError",
    "LogoutResult",
    "NoSessionError",
    "NotFoundError",
    "OdoucheError",
    "OutcomeUnknownError",
    "PermissionDeniedError",
    "Project",
    "ReadOnlyError",
    "Secret",
    "SessionExpiredError",
    "SessionInfo",
    "SessionSource",
    "SshTarget",
    "Stage",
    "StageRefusedError",
    "StreamTimeoutError",
    "UpstreamChangedError",
    "UpstreamUnavailableError",
    "__version__",
    "login",
    "logout",
]

__version__ = version("odouche")
"""The version of the installed `odouche` distribution."""
