"""Startup configuration parsing.

Reads the four runtime inputs defined by plan §4:

- ``HOST_WORKSPACE_DIR``   — root of operator persistent data
- ``MAGI_NAME``            — MAGI display name (default ``eva-000``)
- ``MAGIS_DATABASE_URL``   — MAGIS DSN (omit ⇒ bootstrap first MAGIS)
- ``MAGI_ID``              — MAGIS identity when joining an existing MAGIS

Derives the final :attr:`StartupConfig.workspace_dir` from
``HOST_WORKSPACE_DIR + MAGI_NAME`` per plan §6 — callers cannot pass a
final workspace path.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

from magi.launcher.paths import default_data_root

# Fixed first-MAGI name per plan §2.2.
DEFAULT_MAGI_NAME = "eva-000"

# Canonical on-disk folder names per plan §9.
MAGI_CITIZENS_DIR = "MAGI_Citizens"
MAGI_SOCIETIES_DIR = "MAGI_Societies"


class ConfigurationError(ValueError):
    """Raised when a startup configuration is internally inconsistent."""


@dataclass(frozen=True)
class StartupConfig:
    """Resolved startup configuration for one MAGI process.

    The final :attr:`workspace_dir` is always derived from
    ``host_workspace_dir + magi_name``; no caller can override it.
    """

    host_workspace_dir: Path
    magi_name: str
    magis_database_url: str | None
    magi_id: str | None

    @property
    def workspace_dir(self) -> Path:
        """Per plan §6: ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>``."""
        return self.host_workspace_dir / MAGI_CITIZENS_DIR / self.magi_name

    @property
    def is_first_magi(self) -> bool:
        """True when this process is bootstrapping the very first MAGIS."""
        return self.magis_database_url is None

    @property
    def is_existing_magi(self) -> bool:
        """True when this process joins an already-bootstrapped MAGIS."""
        return self.magis_database_url is not None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "StartupConfig":
        """Build config from process environment.

        CLI flags should pass through :meth:`from_cli`; ``__main__``
        does not pass ``argv`` here.
        """
        return cls.from_cli(None)

    @classmethod
    def from_cli(
        cls,
        argv: list[str] | None,
        *,
        defaults: dict[str, object] | None = None,
    ) -> "StartupConfig":
        """Build config from CLI flags + env vars.

        Unknown flags pass through untouched (the CLI layer re-parses
        with its own argparse).
        """
        defaults = dict(defaults or {})
        env = os.environ

        host_raw = defaults.get("host_workspace_dir")
        if host_raw is None:
            host_raw = env.get("HOST_WORKSPACE_DIR") or default_data_root()
        host = Path(host_raw).expanduser().resolve()

        name = defaults.get("magi_name")
        if name is None:
            name = env.get("MAGI_NAME") or DEFAULT_MAGI_NAME
        cls._validate_name(name)

        magis_url = defaults.get("magis_database_url")
        if magis_url is None:
            magis_url = env.get("MAGIS_DATABASE_URL") or None
        magis_url = cls._normalise_url(magis_url) if magis_url else None

        magi_id = defaults.get("magi_id")
        if magi_id is None:
            magi_id = env.get("MAGI_ID") or None
        if magi_id is not None:
            magi_id = str(magi_id).strip()
            if not magi_id:
                magi_id = None

        cfg = cls(
            host_workspace_dir=host,
            magi_name=str(name),
            magis_database_url=magis_url,
            magi_id=magi_id,
        )
        cls._validate(cfg)
        return cfg

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("MAGI_NAME must be a non-empty string")
        slug = name.strip().lower()
        slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
        if not slug:
            raise ConfigurationError(f"invalid MAGI_NAME: {name!r}")

    @staticmethod
    def _normalise_url(url: str) -> str:
        url = url.strip()
        # Allow sqlite relative paths to be passed as absolute; leave the
        # string alone otherwise — driver-prefix parsing lives in SQLAlchemy.
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            # sqlite:///relative — make absolute by joining cwd only if
            # the path is not already absolute; preserve behaviour.
            tail = url[len("sqlite:///"):]
            if tail and not tail.startswith("/"):
                url = f"sqlite:///{Path(tail).resolve()}"
        return url

    @staticmethod
    def _validate(cfg: "StartupConfig") -> None:
        if cfg.is_existing_magi and not cfg.magi_id:
            raise ConfigurationError(
                "MAGI_ID is required when joining an existing MAGIS "
                "(MAGIS_DATABASE_URL is set)"
            )


__all__ = [
    "ConfigurationError",
    "StartupConfig",
    "DEFAULT_MAGI_NAME",
    "MAGI_CITIZENS_DIR",
    "MAGI_SOCIETIES_DIR",
]