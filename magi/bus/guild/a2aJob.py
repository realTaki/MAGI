"""MAGIS-backed durable A2A request and notification queues.

These boards are deliberately instantiated with the shared MAGIS factory,
not a MAGI-local store.  A receiver claims only rows addressed to its own
``magis_memberships.id``; no HTTP channel or transport is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, _read_result_from_job, _row_to_job, new_job_id
from magi.bus.library.magis.membershipBook import _MagisMembershipRow


@dataclass(frozen=True, slots=True)
class A2ARequestJob:
    job_id: str = ""  # 发布时自动生成的 job_id
    source_magi_id: int = 0  # 发送方 MAGI 身份（指向 magis_memberships.id）
    target_magi_id: int = 0  # 接收方 MAGI 身份（仅 target 可 claim）
    tool_call_id: str = ""  # 关联的 tool_call_id
    conversation_id: str | None = None  # 可选的会话 ID 透传
    correlation_id: str | None = None  # 跨系统追踪 ID
    text: str = ""  # 请求正文
    payload: dict | None = None  # 额外的 JSON 结构化负载
    deadline_at: datetime | None = None  # 超时截止时间；到期自动失败


@dataclass(frozen=True, slots=True)
class A2ARequestResult:
    job_id: str = ""  # 对应 A2ARequestJob 的 job_id
    success: bool = False  # 请求是否被成功处理
    content: str = ""  # 目标 MAGI 回传的响应文本
    error_code: str = ""  # 稳定错误码（如 a2a_timeout）
    error: str | None = None  # 失败时的错误文案
    tool_call_id: str = ""  # 回传的 tool_call_id


@dataclass(frozen=True, slots=True)
class A2ANotifyJob:
    job_id: str = ""  # 发布时自动生成的 job_id
    source_magi_id: int = 0  # 发送方 MAGI 身份
    target_magi_id: int = 0  # 接收方 MAGI 身份（仅 target 可 claim）
    conversation_id: str | None = None  # 可选的会话 ID 透传
    correlation_id: str | None = None  # 跨系统追踪 ID
    text: str = ""  # 通知正文
    payload: dict | None = None  # 额外的 JSON 结构化负载


@dataclass(frozen=True, slots=True)
class A2ANotifyResult:
    job_id: str = ""  # 对应 A2ANotifyJob 的 job_id
    success: bool = False  # 投递是否成功
    error_code: str = ""  # 稳定错误码
    error: str | None = None  # 失败时的错误文案


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
            self._expire_due(s, target_magi_id=magi_id)
            row = self._cas_claim(
                s,
                owner=f"a2a-request:{magi_id}:{id(self)}",
                extra_where=[_A2ARequestRow.target_magi_id == magi_id],
            )
            s.commit()
            return _row_to_job(row, A2ARequestJob) if row is not None else None

    def submit_result(self, *, key: str, result: A2ARequestResult) -> None:
        """Complete a request once, and never overwrite expiry/failure."""
        with self._session() as s:
            row = s.scalar(select(_A2ARequestRow).where(_A2ARequestRow.job_id == key))
            if row is None:
                return
            self._expire_due(s, target_magi_id=row.target_magi_id)
            s.refresh(row)
            if row.status != "processing":
                s.commit()
                return
            self._submit(s, key=key, result=result)
            s.commit()

    def get_result(self, *, key: str) -> A2ARequestResult | None:
        with self._session() as s:
            row = s.scalar(select(_A2ARequestRow).where(_A2ARequestRow.job_id == key))
            if row is None:
                return None
            self._expire_due(s, target_magi_id=row.target_magi_id)
            s.refresh(row)
            if row.status not in {"completed", "failed"}:
                s.commit()
                return None
            result = _read_result_from_job(row, A2ARequestResult, self.natural_key_attr)
            s.commit()
            return result

    @staticmethod
    def _expire_due(session, *, target_magi_id: int) -> None:
        now = utcnow_naive()
        session.execute(
            update(_A2ARequestRow)
            .where(
                _A2ARequestRow.target_magi_id == target_magi_id,
                _A2ARequestRow.deadline_at.is_not(None),
                _A2ARequestRow.deadline_at <= now,
                _A2ARequestRow.status.in_(("pending", "processing")),
            )
            .values(
                status="failed",
                error_code="a2a_timeout",
                error="A2A request deadline elapsed",
                completed_at=now,
            )
        )


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
