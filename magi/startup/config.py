"""Startup configuration — the single source of truth for runtime inputs.

Per the refactor plan, exactly four inputs define a MAGI startup:

- ``HOST_WORKSPACE_DIR`` — root of operator persistent data (default ``~/.magi``)
- ``MAGI_NAME``            — display name (default ``eva-000``)
- ``MAGIS_DATABASE_URL``   — MAGIS DSN (omit ⇒ bootstrap first MAGIS)
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

MAGI_CITIZENS_DIR = "MAGI_Citizens"
"""Canonical on-disk folder name for per-MAGI workspaces (plan §9)."""


# ------------------------------------------------------------------
# StartupConfig
# ------------------------------------------------------------------


@dataclass(frozen=True)
class StartupConfig:
    """Immutable startup configuration.

    All four fields are read from environment or CLI.  The workspace
    directory is derived — callers must not supply it directly.
    """

    host_workspace_dir: Path
    magi_name: str
    magis_database_url: str | None
    magi_id: str | None

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

        Per plan §3: the absence of MAGIS_DATABASE_URL means this is the
        first MAGI (``eva-000``) that must create the MAGIS database.
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
        - ``MAGIS_DATABASE_URL`` → ``None`` (bootstrap first MAGIS)
        - ``MAGI_ID``            → ``None``
        """
        host_raw = os.environ.get(
            "HOST_WORKSPACE_DIR",
            str(Path.home() / ".magi"),
        )
        host = Path(host_raw).expanduser().resolve()

        magi_name = os.environ.get("MAGI_NAME", "eva-000")

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
        )

    @classmethod
    def from_cli(
        cls,
        *,
        host_workspace_dir: str | Path | None = None,
        magi_name: str | None = None,
        magis_database_url: str | None = None,
        magi_id: str | None = None,
    ) -> StartupConfig:
        """Build config from explicit CLI arguments.

        Unset arguments fall back to environment defaults (via :meth:`from_env`).
        """
        base = cls.from_env()
        return cls(
            host_workspace_dir=Path(host_workspace_dir) if host_workspace_dir else base.host_workspace_dir,
            magi_name=magi_name or base.magi_name,
            magis_database_url=magis_database_url if magis_database_url is not None else base.magis_database_url,
            magi_id=magi_id if magi_id is not None else base.magi_id,
        )

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate the configuration combination.

        Raises :class:`ConfigurationError` on invalid combinations.
        """
        # MAGIS provided but no MAGI_ID → ambiguous identity
        if self.magis_database_url is not None and self.magi_id is None:
            raise ConfigurationError(
                "MAGI_ID is required when joining an existing MAGIS "
                "(MAGIS_DATABASE_URL is set)."
            )

        # First MAGI must be eva-000 when bootstrapping
        if self.magis_database_url is None and self.magi_name != DEFAULT_MAGI_NAME:
            raise ConfigurationError(
                f"The first MAGI must be 'eva-000', got {self.magi_name!r}. "
                "To join an existing MAGIS, set MAGIS_DATABASE_URL and MAGI_ID."
            )

        # Host workspace must exist or be creatable
        # (we validate lazily — the bootstrap step creates dirs)

        # MAGI name must be a valid slug
        if not self.magi_name or " " in self.magi_name:
            raise ConfigurationError(
                f"Invalid MAGI name: {self.magi_name!r}. "
                "Name must be non-empty and contain no spaces."
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
# public API
# ------------------------------------------------------------------

__all__ = [
    "StartupConfig",
    "ConfigurationError",
    "DEFAULT_MAGI_NAME",
    "MAGI_CITIZENS_DIR",
]
