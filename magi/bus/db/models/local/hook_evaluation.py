"""ORM table: hook_evaluations (per-handler evaluation audit row).

The table is the durable record of "what did the runtime decide
when handler X evaluated envelope Y".  One row is written per
(handler, hook_event_id) pair, so a single envelope that fans
out to three handlers produces three rows.

The unique constraint is
``(hook_event_id, hook_id, hook_version)`` — re-evaluation by the
same handler version returns the cached row; a new
``hook_version`` triggers a fresh evaluation and inserts a new
row (spec §13).
"""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class HookEvaluation(Base):
    __tablename__ = "hook_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hook_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hook_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hook_version: Mapped[str] = mapped_column(String(32), nullable=False)
    hook_point: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    started_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    completed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sanitized_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)

    __table_args__ = (
        UniqueConstraint(
            "hook_event_id", "hook_id", "hook_version",
            name="uq_hook_evaluations_event_hook_version",
        ),
        Index("ix_hook_evaluations_subject", "subject_type", "subject_id"),
        Index("ix_hook_evaluations_point_status", "hook_point", "status"),
        Index("ix_hook_evaluations_created", "created_at"),
    )


__all__ = ["HookEvaluation"]
