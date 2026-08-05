"""ORM table: control_jobs (transient provider-config refresh signal).

A control job is a short-lived BUS-to-worker signal — the consumer
deletes the row as soon as it has acted on it, so this table is a
queue, not an audit log. ``llm_attempts`` and ``hook_evaluations``
own the durable trace; this table only carries "wake up and refresh
your cached config".

Today the only kind is ``provider.config_changed``; the worker
drains that kind on every poll tick and rebuilds its cached
``LLMProvider`` if at least one row was deleted. Adding a second
kind requires extending :class:`magi.bus.protocols.control_jobs.ControlJobKind`
and the consumer's drain call — the model itself stays generic.
"""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class ControlJob(Base):
    __tablename__ = "control_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Closed :class:`ControlJobKind` literal; stored as a free string
    # so a future kind doesn't require a migration.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Debug-only payload (e.g. {"provider": "claude", "model": "..."}).
    # MUST NOT carry the API key — the consumer re-reads the file.
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)

    __table_args__ = (
        # Drain selects by kind and orders by id; this index makes that
        # a covering scan regardless of how many rows accumulate.
        Index("ix_control_jobs_drain", "kind", "id"),
    )