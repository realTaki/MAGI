"""Storage profile selection (Composition-Root config object).

The :class:`StorageProfile` decides how the MAGIS public schema is
provisioned.  Today only :class:`KubernetesStorageProfile` (PostgreSQL
via ``MAGIS_DATABASE_URL``) is wired; the Local Profile ships in Phase
3 and selects :class:`LocalStorageProfile` (per-MAGIS SQLite).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine


@dataclass(frozen=True, slots=True)
class StorageProfile(ABC):
    """Abstract base — one concrete subclass per deployment profile."""

    kind: str

    @abstractmethod
    def magis_engine(self, magis_dir: Path) -> Engine:
        """Build (or return a cached) SQLAlchemy engine for the MAGIS schema.

        Called by the Composition Root when the BUS is assembled.  The
        returned engine is injected into
        :mod:`magi.bus.db.magis.engine` so the business modules never
        see the underlying DSN / file path.
        """
        ...


@dataclass(frozen=True, slots=True)
class KubernetesStorageProfile(StorageProfile):
    """K8s Profile — PostgreSQL via ``MAGIS_DATABASE_URL``."""

    kind: str = "kubernetes"

    def magis_engine(self, magis_dir: Path) -> Engine:
        import os

        from magi.bus.db.magis.engine import get_magis_engine

        # Honour ``MAGIS_DATABASE_URL`` exactly like today.
        _ = magis_dir  # K8s profile doesn't use the local dir hint.
        if not os.environ.get("MAGIS_DATABASE_URL"):
            raise RuntimeError(
                "KubernetesStorageProfile requires MAGIS_DATABASE_URL to be set"
            )
        return get_magis_engine()


@dataclass(frozen=True, slots=True)
class LocalStorageProfile(StorageProfile):
    """Local Profile — per-MAGIS SQLite with WAL + busy_timeout + FK."""

    kind: str = "local"

    def magis_engine(self, magis_dir: Path) -> Engine:
        from magi.bus.db.magis.local_engine import LocalMagisEngine

        return LocalMagisEngine.build(magis_dir)


__all__ = [
    "StorageProfile",
    "KubernetesStorageProfile",
    "LocalStorageProfile",
]