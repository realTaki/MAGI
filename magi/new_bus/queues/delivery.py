"""DeliveryQueue — committed channel delivery outbox.

Backed by the ``deliveries`` table (parallel to old bus's
``magi.bus.db.models.queue.delivery_outbox.DeliveryOutbox``).  Natural
key is ``delivery_id``.

Supports ``inline=True``: when the destination is an in-process
channel (e.g. a registered dispatcher adapter), the inline
handler dispatches the message directly via a caller-injected
``dispatch_callback`` and writes back the result synchronously.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import JSON, DateTime, Index, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.queues.base import BaseJobQueue, new_job_id


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    """Publisher input — one row per channel delivery."""

    delivery_id: str = ""
    run_id: str | None = None
    channel: str = "tg"
    destination: str | None = None
    event_id: str | None = None
    payload: dict[str, Any] | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryJobResult:
    """Worker output — final state of one delivery."""

    delivery_id: str = ""
    success: bool = False
    status: str = "failed"
    error: str = ""


# -- internal ORM --------------------------------------------------------


class _DeliveryOutboxRow(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    available_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (Index("ix_deliveries_event_id", "event_id"),)


# -- Queue ----------------------------------------------------------------


class DeliveryQueue(BaseJobQueue[_DeliveryOutboxRow, DeliveryJob, DeliveryJobResult]):
    job_model = _DeliveryOutboxRow
    job_cls = DeliveryJob
    result_cls = DeliveryJobResult
    natural_key_attr = "delivery_id"

    def __init__(
        self,
        factory,
        lease_seconds: int = 60,
        dispatch_callback: Callable[[str, str | None, dict[str, Any]], None] | None = None,
    ):
        super().__init__(factory, lease_seconds=lease_seconds)
        self._dispatch_callback = dispatch_callback

    def _insert_pending(self, session, job: DeliveryJob, **kwargs) -> _DeliveryOutboxRow:
        delivery_id = job.delivery_id or new_job_id()
        row = _DeliveryOutboxRow(
            delivery_id=delivery_id,
            run_id=job.run_id,
            channel=job.channel,
            destination=job.destination,
            event_id=job.event_id,
            payload=job.payload or {},
            idempotency_key=job.idempotency_key,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row

    def _run_inline(self, session: Session, *, job_id, **kwargs) -> DeliveryJobResult:
        """Inline-publish: dispatch directly via callback (in-process channel)."""
        row = session.get(_DeliveryOutboxRow, job_id)
        if row is None:
            return DeliveryJobResult(delivery_id=job_id, success=False, error="row not found")
        try:
            if self._dispatch_callback is not None:
                self._dispatch_callback(row.channel, row.destination, row.payload or {})
            row.status = "completed"
            return DeliveryJobResult(delivery_id=job_id, success=True, status="completed")
        except Exception as exc:  # noqa: BLE001
            row.status = "failed"
            return DeliveryJobResult(delivery_id=job_id, success=False, error=str(exc))


__all__ = [
    "DeliveryJob",
    "DeliveryJobResult",
    "DeliveryQueue",
    "_DeliveryOutboxRow",
]
