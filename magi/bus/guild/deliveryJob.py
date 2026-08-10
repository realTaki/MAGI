"""deliveryJobBoard — 出站投递作业。

agent 产出回复 → 入队 → worker 投递到渠道
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, and_, or_, select, update
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    channel: str
    payload: dict
    destination: str | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    job_id: str
    success: bool
    error: str | None = None


class _DeliveryJobRow(Base):
    __tablename__ = "delivery_outbox"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class deliveryJobBoard(BaseJobBoard[_DeliveryJobRow, DeliveryJob, DeliveryResult]):
    job_model = _DeliveryJobRow
    job_cls = DeliveryJob
    result_cls = DeliveryResult

    def publish(self, job: DeliveryJob) -> str:
        with self._session() as s:
            row = _DeliveryJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                channel=job.channel,
                payload=job.payload,
                destination=job.destination,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id

    def claim_for_channel(self, *, channel: str) -> DeliveryJob | None:
        """CAS-claim the oldest pending delivery row scoped to *channel*.

        Replaces the previous "claim any, release mismatches" pattern
        that caused every channel worker to thrash on rows it didn't
        own (P1 issue in the 2026-08-10 architecture review). Each
        channel worker now reads only its own row slice; no
        claim/release churn, no cross-worker race.
        """
        from datetime import timedelta

        MAX_ATTEMPTS_CANDIDATES = 10
        MAX_ATTEMPTS = 10
        owner = f"delivery:{channel}:{id(self)}"
        now = utcnow_naive()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        with self._session() as s:
            for _ in range(MAX_ATTEMPTS_CANDIDATES):
                # Find oldest pending / lease-expired row for this channel.
                candidate = s.scalar(
                    select(_DeliveryJobRow)
                    .where(
                        _DeliveryJobRow.channel == channel,
                        or_(
                            _DeliveryJobRow.status == "pending",
                            and_(
                                _DeliveryJobRow.status == "processing",
                                _DeliveryJobRow.leased_until < now,
                            ),
                        ),
                    )
                    .order_by(_DeliveryJobRow.created_at, _DeliveryJobRow.id)
                    .limit(1)
                )
                if candidate is None:
                    return None
                # MAX_ATTEMPTS exhaustion — mark failed, move on.
                if candidate.status == "processing" and candidate.attempts >= MAX_ATTEMPTS:
                    exhausted = DeliveryResult(
                        job_id=candidate.job_id, success=False,
                        error=f"job exhausted after {candidate.attempts} attempt(s)",
                    )
                    s.execute(
                        update(_DeliveryJobRow)
                        .where(_DeliveryJobRow.id == candidate.id)
                        .values(
                            status="failed",
                            completed_at=now,
                            result={"success": False, "error": exhausted.error},
                        )
                    )
                    s.commit()
                    continue
                is_reclaim = candidate.status == "processing"
                # Atomic CAS UPDATE on the same row + channel + invariants.
                result = s.execute(
                    update(_DeliveryJobRow)
                    .where(
                        _DeliveryJobRow.id == candidate.id,
                        _DeliveryJobRow.channel == channel,
                        or_(
                            _DeliveryJobRow.status == "pending",
                            and_(
                                _DeliveryJobRow.status == "processing",
                                _DeliveryJobRow.leased_until < now,
                            ),
                        ),
                    )
                    .values(
                        status="processing",
                        leased_by=owner,
                        leased_until=lease_until,
                        attempts=candidate.attempts + 1,
                        started_at=now if not is_reclaim else candidate.started_at,
                    )
                )
                if getattr(result, "rowcount", 0) == 1:
                    s.commit()
                    fresh = s.get(_DeliveryJobRow, candidate.id)
                    if fresh is None:
                        return None
                    return DeliveryJob(
                        channel=fresh.channel,
                        payload=fresh.payload,
                        destination=fresh.destination,
                        job_id=fresh.job_id,
                    )
                # Lost the race — try next.
                s.rollback()
                now = utcnow_naive()
                lease_until = now + timedelta(seconds=self._lease_seconds)
            return None
