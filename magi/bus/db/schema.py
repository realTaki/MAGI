"""Internal schema revision materialisation owned by BUS provisioning."""

from __future__ import annotations

from magi.bus.db.base import Base
from magi.bus.db.engine import EngineFactory


def apply_initial_schema(factory: EngineFactory) -> None:
    """Materialise the current initial schema during explicit provisioning only."""
    Base.metadata.create_all(factory.engine)


__all__ = ["apply_initial_schema"]
