"""ORM table: hook_signoffs (pending async plugin acknowledgements).

Replaces the OLD :class:`HookEvaluation` inline-hook audit table.
The new flow is asynchronous + tag-based:

  - When a bus.store boundary method (``enqueue_llm_job`` etc.)
    commits a durable row, it inserts one ``hook_signoffs`` row
    per (subject, plugin) pair for every plugin whose
    ``hook_plugin_configs.hook_points`` list contains the
    matching hook point.  The row's ``pending`` flag is ``1``.

  - Downstream workers (provider / tool / delivery) refuse to
    claim jobs whose subject_type + subject_id still has a
    pending signoff.  The claim query joins with
    ``NOT EXISTS (SELECT 1 FROM hook_signoffs WHERE pending=1)``.

  - Plugins poll ``claim_pending_signoffs(plugin_id)`` to pull
    their own pending rows, process the related job, and then
    call ``ack_signoff(signoff_id)`` to flip the flag to ``0``
    and stamp ``acked_at``.  When the last pending row clears,
    the downstream worker can claim the job.

Unique constraint on ``(subject_type, subject_id, hook_point,
plugin_id)`` is what makes the dispatch idempotent: re-enqueueing
the same subject (e.g. via a retry row) does not double-fire
the same plugin.
"""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class HookSignoff(Base):
    __tablename__ = "hook_signoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hook_point: Mapped[str] = mapped_column(String(64), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    acked_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_id", "hook_point", "plugin_id",
            name="uq_hook_signoffs_subject_plugin",
        ),
        Index("ix_hook_signoffs_pending", "plugin_id", "pending", "created_at"),
        Index("ix_hook_signoffs_subject", "subject_type", "subject_id"),
    )
