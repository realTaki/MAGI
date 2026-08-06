"""McpServerJob — writes to the ``mcp_servers`` table."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import JSON, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive

logger = logging.getLogger("magi.new_bus.jobs.mcp")


class _JMcpServerRow(JobBase):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(nullable=False, unique=True)
    transport: Mapped[str] = mapped_column(nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at = mapped_column(default=job_utcnow_naive, nullable=False)
    updated_at = mapped_column(
        default=job_utcnow_naive, onupdate=job_utcnow_naive, nullable=False,
    )


class McpServerJob(BaseJob):
    """Write side of the MCP-server domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(
        self,
        *,
        name: str,
        transport: str,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> int:
        with self._factory.session() as s:
            row = _JMcpServerRow(
                name=name, transport=transport,
                config=config or {},
                enabled=1 if enabled else 0,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def update(
        self,
        *,
        server_id: int,
        transport: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JMcpServerRow).where(_JMcpServerRow.id == server_id)
            )
            if row is None:
                return
            if transport is not None:
                row.transport = transport
            if config is not None:
                row.config = config
            if enabled is not None:
                row.enabled = 1 if enabled else 0
            s.commit()

    def delete(self, *, server_id: int) -> bool:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JMcpServerRow).where(_JMcpServerRow.id == server_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["McpServerJob"]
