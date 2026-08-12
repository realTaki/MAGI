"""MAGIS-backed durable A2A request and notification queues.

These boards are deliberately instantiated with the shared MAGIS factory,
not a MAGI-local store.  A receiver claims only rows addressed to its own
``magis_memberships.id``; no HTTP channel or transport is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, JobRowMixin, _read_result_from_job
from magi.bus.library.magis.membershipBook import _MagisMembershipRow


# -- public enum ---------------------------------------------------------


class A2AErrorCode(StrEnum):
    """Stable, A2A-board-managed error codes.

    ``StrEnum`` rather than bare string constants so the membership
    check raises on lookup instead of silently comparing False.
    Every member is still a ``str``
    (``A2AErrorCode.TIMEOUT == "a2a_timeout"``), so JSON
    serialisation, ``==`` / ``!=`` against string literals and any
    remaining ``String`` columns keep working unchanged. The A2A
    tables' ``error_code`` column is now a native
    :class:`~sqlalchemy.types.Enum` of this class — PG stores the
    ENUM type's OID, SQLite stores the value behind a CHECK
    constraint, both endpoints hand back :class:`A2AErrorCode`
    members on read. Mirrors
    :class:`magi.bus.guild.mcpServerChangedJob.MCPKind` /
    :class:`magi.bus.library.local.actionItemBook.ActionSource`.

    When the target MAGI rejects a request with its own
    business-layer code, the caller should add a member here
    rather than inventing a new literal — that's the whole point
    of the comment "稳定错误码".
    """

    TIMEOUT = "a2a_timeout"  # Request reached ``deadline_at`` without a result


@dataclass(frozen=True, slots=True)
class A2ARequestJob(BaseJob):
    """MAGIS 间的可观测 A2A 请求："一问一答"，target claim 后必须回执一次。

    由 ``a2aRequestJobBoard.publish`` 持久化到 ``a2a_request_jobs``；
    只有 ``target_magi_id`` 对应的 MAGI 通过 ``claim_for_target``
    能拿到这条 job，``source_magi_id`` 只作为审计字段。``deadline_at``
    到达后 worker 通过 ``a2aRequestJobBoard._expire_due`` 自动写
    ``status="failed"`` + ``error_code=A2AErrorCode.TIMEOUT``。
    """

    source_magi_id: int = 0  # 发送方 MAGI 身份（指向 magis_memberships.id）
    target_magi_id: int = 0  # 接收方 MAGI 身份（仅 target 可 claim）
    conversation_id: str | None = None  # 可选的会话 ID 透传
    correlation_id: str | None = None  # 跨系统追踪 ID
    text: str = ""  # 请求正文
    deadline_at: datetime | None = None  # 超时截止时间；到期自动失败


@dataclass(frozen=True, slots=True)
class A2ARequestResult(BaseJobResult):
    """Target MAGI 处理 :class:`A2ARequestJob` 后的回执。

    ``success=True`` 表示 target 接受了请求并填了 ``content``
    回传响应；``success=False`` 时 ``error_code`` 是 :class:`A2AErrorCode`
    中的稳定错误码（``TIMEOUT`` / 业务码），``error`` 是给人
    看的文案。
    """

    content: str = ""  # 目标 MAGI 回传的响应文本
    error_code: A2AErrorCode | None = None  # 稳定错误码（来自 A2AErrorCode）
    error: str | None = None  # 失败时的错误文案


@dataclass(frozen=True, slots=True)
class A2ANotifyJob(BaseJob):
    """MAGIS 间的单向通知："发了就算"，target 异步消化，发布方不等待回执。

    持久化到 ``a2a_notify_jobs``；同样只有 ``target_magi_id`` 对应
    的 MAGI 能 claim。``a2aNotifyBoard`` 只暴露 ``publish`` /
    ``claim_for_target``，没有 :meth:`BaseJobBoard.get_result` —
    投递结果只写到 ``status`` / ``error_code``，调用方按业务需
    要轮询而非常规 result 路径。
    """

    source_magi_id: int = 0  # 发送方 MAGI 身份
    target_magi_id: int = 0  # 接收方 MAGI 身份（仅 target 可 claim）
    conversation_id: str | None = None  # 可选的会话 ID 透传
    correlation_id: str | None = None  # 跨系统追踪 ID
    text: str = ""  # 通知正文


@dataclass(frozen=True, slots=True)
class A2ANotifyResult(BaseJobResult):
    """:class:`A2ANotifyJob` 的终端回执 — 仅在通知被消费且出错时落库。

    没有超时路径（notify 不阻塞发送方），也没有强制的 ``content``
    字段：``success=False`` 时 ``error_code`` 取自 :class:`A2AErrorCode`，
    ``error`` 描述投递失败原因，成功则通常只更新 ``status`` 而不
    构造 Result。
    """

    error_code: A2AErrorCode | None = None  # 稳定错误码（来自 A2AErrorCode）
    error: str | None = None  # 失败时的错误文案


class _A2ARequestRow(JobRowMixin, Base):
    __tablename__ = "a2a_request_jobs"
    __table_args__ = (
        Index("ix_a2a_request_target_status_available", "target_magi_id", "status", "available_at"),
        {"extend_existing": True},
    )

    source_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    target_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[A2AErrorCode | None] = mapped_column(
        Enum(
            A2AErrorCode,
            name="a2a_error_code",
            native_enum=True,
            length=64,
            # SQLAlchemy 2.x dropped the implicit CHECK on Enum
            # (was ``True`` in 1.x); SQLite has no native ENUM
            # so without this the column is just VARCHAR with
            # no membership enforcement.
            create_constraint=True,
            # ``StrEnum`` has both ``name`` (``TIMEOUT``) and
            # ``value`` (``"a2a_timeout"``); SQLAlchemy defaults
            # to ``name`` for storage, but pre-Enum rows hold
            # the ``value`` string verbatim — using ``name`` here
            # would silently rename existing data. ``values_callable``
            # forces storage / CHECK against the stable ``value``.
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=True,
        default=None,
    )
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class _A2ANotifyRow(JobRowMixin, Base):
    __tablename__ = "a2a_notify_jobs"
    __table_args__ = (
        Index("ix_a2a_notify_target_status_available", "target_magi_id", "status", "available_at"),
        {"extend_existing": True},
    )

    source_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    target_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[A2AErrorCode | None] = mapped_column(
        Enum(
            A2AErrorCode,
            name="a2a_error_code",
            native_enum=True,
            length=64,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=True,
        default=None,
    )
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
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
            job_id = job.job_id or self.new_job_id()
            existing = s.scalar(select(_A2ARequestRow).where(_A2ARequestRow.job_id == job_id))
            if existing is not None:
                s.commit()
                return existing.job_id
            row = _A2ARequestRow(
                job_id=job_id,
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
                conversation_id=job.conversation_id,
                correlation_id=job.correlation_id,
                text=job.text,
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
            return self._map_row(row, A2ARequestJob) if row is not None else None

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
                error_code=A2AErrorCode.TIMEOUT,
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
            job_id = job.job_id or self.new_job_id()
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
            return self._map_row(row, A2ANotifyJob) if row is not None else None

    def submit_result(self, *, key: str, result: A2ANotifyResult) -> None:
        # Dataclass shape (``A2AErrorCode | None``) already matches
        # the native :class:`~sqlalchemy.types.Enum` column, so
        # ``BaseJobBoard.submit_result`` writes the value verbatim.
        # Mirrors :meth:`a2aRequestJobBoard.submit_result`.
        super().submit_result(key=key, result=result)


__all__ = [
    "A2AErrorCode",
    "A2ARequestJob",
    "A2ARequestResult",
    "A2ANotifyJob",
    "A2ANotifyResult",
    "a2aRequestJobBoard",
    "a2aNotifyBoard",
    "_A2ARequestRow",
    "_A2ANotifyRow",
]
