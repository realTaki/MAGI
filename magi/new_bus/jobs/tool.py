"""ToolCatalogJob + ToolDefinitionJob — writes to tool-catalog tables."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase

logger = logging.getLogger("magi.new_bus.jobs.tool")


class _JToolCatalogStateRow(JobBase):
    __tablename__ = "tool_catalog_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(default="", nullable=False)


class _JToolDefinitionRow(JobBase):
    __tablename__ = "tool_definitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    spec_dict: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    enabled: Mapped[int] = mapped_column(default=1, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(default="manual", nullable=False)


class ToolCatalogStateJob(BaseJob):
    """Write side of the tool-catalog state singleton."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def bump(self, *, revision: int, snapshot_hash: str) -> int:
        with self._factory.session() as s:
            row = s.scalar(select(_JToolCatalogStateRow).limit(1))
            if row is None:
                row = _JToolCatalogStateRow(
                    revision=revision, snapshot_hash=snapshot_hash
                )
                s.add(row)
            else:
                row.revision = revision
                row.snapshot_hash = snapshot_hash
            s.commit()
            s.refresh(row)
        return row.id


class ToolDefinitionJob(BaseJob):
    """Write side of the tool-definition domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def upsert(
        self,
        *,
        name: str,
        spec_json: str,
        revision: int = 0,
        description: str | None = None,
        source: str = "manual",
        spec_dict: str | None = None,
    ) -> int:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JToolDefinitionRow).where(_JToolDefinitionRow.name == name)
            )
            if row is None:
                row = _JToolDefinitionRow(
                    name=name, spec_json=spec_json, revision=revision,
                    description=description, source=source, spec_dict=spec_dict,
                )
                s.add(row)
            else:
                row.spec_json = spec_json
                row.revision = revision
                row.description = description
                row.source = source
                row.spec_dict = spec_dict
            s.commit()
            s.refresh(row)
        return row.id


__all__ = ["ToolCatalogStateJob", "ToolDefinitionJob"]
