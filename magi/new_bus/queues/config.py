"""ConfigJobQueue — transient BUS-to-worker refresh signal.

Backed by the ``control_jobs`` table.  Used for fire-and-forget
signals like ``provider.config_changed`` — the consumer drains the
queue and rebuilds its cached config.

Supports ``inline=True``: when the publisher wants to wake the
provider worker synchronously, the inline handler can call a
caller-injected ``rebuild_callback`` (set via the constructor's
``rebuild_callback`` parameter) and write back a
``ConfigJobResult`` with the rebuild status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import JSON, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.queues.base import BaseJobQueue, new_job_id


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigJob:
    """Publisher input — a config-refresh signal.

    ``kind`` discriminates the signal type.  Today only
    ``"provider.config_changed"`` is in use; future kinds (e.g.
    ``"tool_catalog.changed"``) extend the vocabulary.
    """

    kind: str = "provider.config_changed"
    payload: dict[str, Any] | None = None
    id: int = 0  # PK; 0 means "auto-assign on insert"


@dataclass(frozen=True, slots=True)
class ConfigJobResult:
    """Worker output — terminal state of one config-refresh job."""

    id: int = 0
    success: bool = False
    error: str = ""


# -- internal ORM --------------------------------------------------------


class _ConfigJobRow(Base):
    __tablename__ = "control_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, default="provider.config_changed")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
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


# -- Queue ----------------------------------------------------------------


class ConfigJobQueue(BaseJobQueue[_ConfigJobRow, ConfigJob, ConfigJobResult]):
    job_model = _ConfigJobRow
    job_cls = ConfigJob
    result_cls = ConfigJobResult
    natural_key_attr = "id"  # int PK

    def __init__(
        self,
        factory,
        lease_seconds: int = 60,
        rebuild_callback: Callable[[dict[str, Any] | None], None] | None = None,
    ):
        super().__init__(factory, lease_seconds=lease_seconds)
        self._rebuild_callback = rebuild_callback

    def _insert_pending(self, session, job: ConfigJob, **kwargs) -> _ConfigJobRow:
        row = _ConfigJobRow(
            kind=job.kind,
            payload=job.payload,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row

    def drain(self, *, worker_id: str) -> int:
        """Drain all pending jobs for ``worker_id``.

        Returns the count drained.  Inline-publish is the natural
        fit for this Queue — the publisher doesn't expect a
        worker to pick it up later, it just wants the rebuild
        callback to fire.
        """
        count = 0
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ConfigJobRow).where(_ConfigJobRow.status == "pending")
            ).all()
            for row in rows:
                if self._rebuild_callback is not None:
                    try:
                        self._rebuild_callback(row.payload)
                    except Exception as exc:  # noqa: BLE001
                        row.status = "failed"
                        s.flush()
                        count += 1
                        continue
                row.status = "completed"
                s.flush()
                count += 1
            s.commit()
        return count

    def _run_inline(self, session: Session, *, job_id, **kwargs) -> ConfigJobResult:
        """Inline-publish: drain the row synchronously via callback."""
        row = session.get(_ConfigJobRow, int(job_id))
        if row is None:
            return ConfigJobResult(id=int(job_id), success=False, error="row not found")
        try:
            if self._rebuild_callback is not None:
                self._rebuild_callback(row.payload)
            row.status = "completed"
            return ConfigJobResult(id=row.id, success=True)
        except Exception as exc:  # noqa: BLE001
            row.status = "failed"
            return ConfigJobResult(id=row.id, success=False, error=str(exc))


__all__ = [
    "ConfigJob",
    "ConfigJobResult",
    "ConfigJobQueue",
    "_ConfigJobRow",
]
