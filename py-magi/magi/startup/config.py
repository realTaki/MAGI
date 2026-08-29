"""Startup configuration — the single source of truth for runtime inputs.

Per the refactor plan, exactly four inputs define a MAGI startup:

- ``HOST_WORKSPACE_DIR`` — root of operator persistent data (default ``~/.magi``)
- ``MAGI_NAME``            — display name (default ``eva-000``)
- ``MAGIS_NAME``           — stable MAGIS storage name (default ``genesis``)
- ``MAGIS_DATABASE_URL``   — MAGIS DSN (omit ⇒ local SQLite for ``MAGIS_NAME``)
- ``MAGI_ID``              — MAGIS identity when joining an existing MAGIS

Workspace is *derived*, never passed in:
``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>``
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(Exception):
    """Raised when startup configuration is invalid."""


# ------------------------------------------------------------------
# constants
# ------------------------------------------------------------------

DEFAULT_MAGI_NAME = "eva-000"
"""The first MAGI is always ``eva-000`` (plan §2.2)."""

DEFAULT_MAGIS_NAME = "genesis"
"""Stable storage name for the default, locally bootstrapped MAGIS."""

MAGI_CITIZENS_DIR = "MAGI_Citizens"
"""Canonical on-disk folder name for per-MAGI workspaces (plan §9)."""


# Internal Runtime host (loopback on every container/host).
RUNTIME_HOST: str = "127.0.0.1"

# Internal Runtime port (non-WebUI port so the singleton WebUI is the
# only thing the operator can reach).
RUNTIME_PORT: int = 42070

# Singleton WebUI port — the only externally reachable surface in K8s
# (plan §21).  The bind host is *not* a constant: it is resolved at
# runtime from ``MAGI_WEBUI_HOST`` (default ``127.0.0.1``).  Kubernetes
# deployments set the env to ``0.0.0.0`` so the ClusterIP / NodePort can
# forward traffic into the pod; CLI / single-machine installs leave it
# unset and stay loopback-only.
WEBUI_PORT: int = 42069

# Default log level used until the bus setting ``system.log_level``
# is read.
DEFAULT_LOG_LEVEL: str = "info"


# ------------------------------------------------------------------
# StartupConfig
# ------------------------------------------------------------------


@dataclass(frozen=True)
class StartupConfig:
    """Immutable startup configuration.

    All fields are read from environment or CLI.  The workspace
    directory is derived — callers must not supply it directly.
    """

    host_workspace_dir: Path
    magi_name: str
    magis_database_url: str | None
    magi_id: str | None
    magis_name: str = DEFAULT_MAGIS_NAME

    @property
    def workspace_dir(self) -> Path:
        """Derive the MAGI workspace directory.

        Always ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>``.
        Never configurable directly.
        """
        return _resolve_workspace(self.host_workspace_dir, self.magi_name)

    @property
    def is_first_magi(self) -> bool:
        """True when no ``MAGIS_DATABASE_URL`` is set — bootstrap first MAGIS.

        The absence of ``MAGIS_DATABASE_URL`` selects a local SQLite MAGIS
        store at ``MAGI_Societies/<MAGIS_NAME>/magis.db``.
        """
        return self.magis_database_url is None

    # ------------------------------------------------------------------
    # factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> StartupConfig:
        """Build config from environment variables.

        Defaults:
        - ``HOST_WORKSPACE_DIR`` → ``~/.magi``
        - ``MAGI_NAME``          → ``"eva-000"``
        - ``MAGIS_NAME``         → ``"genesis"``
        - ``MAGIS_DATABASE_URL`` → ``None`` (bootstrap first MAGIS)
        - ``MAGI_ID``            → ``None``
        """
        host_raw = os.environ.get(
            "HOST_WORKSPACE_DIR",
            str(Path.home() / ".magi"),
        )
        host = Path(host_raw).expanduser().resolve()

        magi_name = os.environ.get("MAGI_NAME", DEFAULT_MAGI_NAME)
        magis_name = os.environ.get("MAGIS_NAME", DEFAULT_MAGIS_NAME).strip().lower()

        magis_db_url: str | None = os.environ.get("MAGIS_DATABASE_URL")
        if magis_db_url is not None:
            magis_db_url = magis_db_url.strip() or None

        magi_id: str | None = os.environ.get("MAGI_ID")
        if magi_id is not None:
            magi_id = magi_id.strip() or None

        return cls(
            host_workspace_dir=host,
            magi_name=magi_name,
            magis_database_url=magis_db_url,
            magi_id=magi_id,
            magis_name=magis_name,
        )

    @classmethod
    def from_cli(
        cls,
        *,
        host_workspace_dir: str | Path | None = None,
        magi_name: str | None = None,
        magis_database_url: str | None = None,
        magi_id: str | None = None,
        magis_name: str | None = None,
    ) -> StartupConfig:
        """Build config from explicit CLI arguments.

        Unset arguments fall back to environment defaults (via :meth:`from_env`).
        """
        base = cls.from_env()
        return cls(
            host_workspace_dir=Path(host_workspace_dir)
            if host_workspace_dir
            else base.host_workspace_dir,
            magi_name=magi_name or base.magi_name,
            magis_database_url=magis_database_url
            if magis_database_url is not None
            else base.magis_database_url,
            magi_id=magi_id if magi_id is not None else base.magi_id,
            magis_name=magis_name.strip().lower() if magis_name is not None else base.magis_name,
        )

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate the configuration combination.

        Raises :class:`ConfigurationError` on invalid combinations.
        """
        # Host workspace must exist or be creatable
        # (we validate lazily — the bootstrap step creates dirs)

        # MAGI name must be a valid slug
        if not self.magi_name or " " in self.magi_name:
            raise ConfigurationError(
                f"Invalid MAGI name: {self.magi_name!r}. "
                "Name must be non-empty and contain no spaces."
            )
        if (
            not self.magis_name
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in self.magis_name)
            or self.magis_name.startswith("-")
            or self.magis_name.endswith("-")
        ):
            raise ConfigurationError(
                f"Invalid MAGIS name: {self.magis_name!r}. "
                "Use a lowercase letter/digit slug with optional internal hyphens."
            )


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _resolve_workspace(host_workspace_dir: Path, magi_name: str) -> Path:
    """Derive the MAGI workspace from host root and name.

    Pure function — no filesystem access, no env reads.
    """
    return host_workspace_dir / "MAGI_Citizens" / magi_name


# ------------------------------------------------------------------
# StartupContext — post-bootstrap identity handed to the runtime layer
# ------------------------------------------------------------------


@dataclass(frozen=True)
class StartupContext:
    """Resolved post-bootstrap identity handed to the runtime layer.

    Fields map 1:1 to plan §10:

    - ``host_workspace_dir`` — operator's host root
    - ``workspace_dir``      — per-MAGI workspace (derived)
    - ``magi_name``          — display name
    - ``magi_id``            — MAGIS identity (``magis_memberships.id``)
    - ``magis_name``         — stable local-storage name for the MAGIS
    - ``magis_database_url`` — DSN of the MAGIS public database
    - ``private_database_url`` — DSN of this MAGI's private SQLite
    - ``is_first_magi``      — True for the ``eva-000`` Genesis bootstrap
    """

    host_workspace_dir: Path
    workspace_dir: Path
    magi_name: str
    magi_id: str
    magis_name: str
    magis_database_url: str
    private_database_url: str
    is_first_magi: bool
    runtime_port: int

    @property
    def magi_slug(self) -> str:
        """Same as :attr:`magi_name` — names are slugs by plan §4.2."""
        return self.magi_name


# ------------------------------------------------------------------
# public API
# ------------------------------------------------------------------

__all__ = [
    "StartupConfig",
    "StartupContext",
    "ConfigurationError",
    "DEFAULT_MAGI_NAME",
    "DEFAULT_MAGIS_NAME",
    "MAGI_CITIZENS_DIR",
    # constants
    "RUNTIME_HOST",
    "RUNTIME_PORT",
    "WEBUI_PORT",
    "DEFAULT_LOG_LEVEL",
]
