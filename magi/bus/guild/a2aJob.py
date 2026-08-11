"""MAGIS-backed durable A2A request and notification queues.

These boards are deliberately instantiated with the shared MAGIS factory,
not a MAGI-local store.  A receiver claims only rows addressed to its own
``magis_memberships.id``; no HTTP channel or transport is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, _row_to_job, new_job_id
from magi.bus.library.magis.membershipBook import _MagisMembershipRow


@dataclass(frozen=True, slots=True)
class A2ARequestJob:
    job_id: str = ""
    source_magi_id: int = 0
    target_magi_id: int = 0
    tool_call_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None
    text: str = ""
    payload: dict | None = None
    deadline_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class A2ARequestResult:
    job_id: str = ""
    success: bool = False
    content: str = ""
    error_code: str = ""
    error: str | None = None
    tool_call_id: str = ""


@dataclass(frozen=True, slots=True)
class A2ANotifyJob:
    job_id: str = ""
    source_magi_id: int = 0
    target_magi_id: int = 0
    conversation_id: str | None = None
    correlation_id: str | None = None
    text: str = ""
    payload: dict | None = None


@dataclass(frozen=True, slots=True)
class A2ANotifyResult:
    job_id: str = ""
    success: bool = False
    error_code: str = ""
    error: str | None = None


class _A2ARequestRow(Base):
    __tablename__ = "a2a_request_jobs"
    __table_args__ = (
        Index("ix_a2a_request_target_status_available", "target_magi_id", "status", "available_at"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    target_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class _A2ANotifyRow(Base):
    __tablename__ = "a2a_notify_jobs"
    __table_args__ = (
        Index("ix_a2a_notify_target_status_available", "target_magi_id", "status", "available_at"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    target_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


def _validate_route(session, *, source_magi_id: int, target_magi_id: int) -> None:
    if source_magi_id <= 0 or target_magi_id <= 0:
        raise ValueError("source_magi_id and target_magi_id are required")
    if source_magi_id == target_magi_id:
        raise ValueError("A2A cannot target the sending MAGI")
    source = session.scalar(
        select(_MagisMembershipRow).where(_MagisMembershipRow.id == source_magi_id)
    )
    target = session.scalar(
        select(_MagisMembershipRow).where(_MagisMembershipRow.id == target_magi_id)
    )
    if source is None or target is None:
        raise LookupError("A2A source or target MAGI does not exist")
    if source.magis_id != target.magis_id:
        raise ValueError("A2A source and target must belong to the same MAGIS")


class a2aRequestJobBoard(BaseJobBoard[_A2ARequestRow, A2ARequestJob, A2ARequestResult]):
    """One request, one terminal response, claimed only by its target MAGI."""

    job_model = _A2ARequestRow
    job_cls = A2ARequestJob
    result_cls = A2ARequestResult

    def publish(self, job: A2ARequestJob) -> str:
        if not job.text.strip():
            raise ValueError("A2A request text is required")
        with self._session() as s:
            _validate_route(
                s,
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
            )
            job_id = job.job_id or new_job_id()
            existing = s.scalar(select(_A2ARequestRow).where(_A2ARequestRow.job_id == job_id))
            if existing is not None:
                s.commit()
                return existing.job_id
            row = _A2ARequestRow(
                job_id=job_id,
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
                tool_call_id=job.tool_call_id,
                conversation_id=job.conversation_id,
                correlation_id=job.correlation_id,
                text=job.text,
                payload=job.payload,
                deadline_at=job.deadline_at,
            )
            s.add(row)
            s.commit()
            return job_id

    def claim_for_target(self, *, magi_id: int) -> A2ARequestJob | None:
        with self._session() as s:
            row = self._cas_claim(
                s,
                owner=f"a2a-request:{magi_id}:{id(self)}",
                extra_where=[_A2ARequestRow.target_magi_id == magi_id],
            )
            s.commit()
            return _row_to_job(row, A2ARequestJob) if row is not None else None


class a2aNotifyBoard(BaseJobBoard[_A2ANotifyRow, A2ANotifyJob, A2ANotifyResult]):
    """Reliable one-way notification; publishers never wait for its result."""

    job_model = _A2ANotifyRow
    job_cls = A2ANotifyJob
    result_cls = A2ANotifyResult

    def publish(self, job: A2ANotifyJob) -> str:
        if not job.text.strip():
            raise ValueError("A2A notification text is required")
        with self._session() as s:
            _validate_route(
                s,
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
            )
            job_id = job.job_id or new_job_id()
            existing = s.scalar(select(_A2ANotifyRow).where(_A2ANotifyRow.job_id == job_id))
            if existing is not None:
                s.commit()
                return existing.job_id
            row = _A2ANotifyRow(
                job_id=job_id,
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
                conversation_id=job.conversation_id,
                correlation_id=job.correlation_id,
                text=job.text,
                payload=job.payload,
            )
            s.add(row)
            s.commit()
            return job_id

    def claim_for_target(self, *, magi_id: int) -> A2ANotifyJob | None:
        with self._session() as s:
            row = self._cas_claim(
                s,
                owner=f"a2a-notify:{magi_id}:{id(self)}",
                extra_where=[_A2ANotifyRow.target_magi_id == magi_id],
            )
            s.commit()
            return _row_to_job(row, A2ANotifyJob) if row is not None else None


__all__ = [
    "A2ARequestJob",
    "A2ARequestResult",
    "A2ANotifyJob",
    "A2ANotifyResult",
    "a2aRequestJobBoard",
    "a2aNotifyBoard",
    "_A2ARequestRow",
    "_A2ANotifyRow",
]
