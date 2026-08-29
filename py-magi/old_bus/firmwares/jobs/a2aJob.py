"""MAGIS-backed durable A2A request and notification queues.

These boards are deliberately instantiated with the shared MAGIS factory,
not a MAGI-local store.  A receiver claims only rows addressed to its own
``magis_memberships.id``; no HTTP channel or transport is involved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.db.base import enum_column
from old_bus.bases.job import (
    BaseJob,
    BaseJobBoard,
    BaseJobResult,
    BaseJobRowMixin,
    JobStatus,
)

if TYPE_CHECKING:
    from old_bus.bases.db.engine import EngineFactory
    from old_bus.firmwares.books.magis.membershipBook import MagisMembershipBook

from old_bus.firmwares.books.local.conversationBook import AgentMessageRole, Message

logger = logging.getLogger("bus.firmwares.jobs.a2aJob")


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
    :class:`bus.firmwares.jobs.changeMCPServerJob.MCPKind` /
    :class:`bus.firmwares.books.local.actionItemBook.ActionSource`.

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
    能拿到这条 job，``source_magi_id`` 只作为审计字段。``deadline_at`` 是
    调用方的业务约束；BUS 保留该字段但不会自行将 job 标为失败。
    """

    source_magi_id: int = 0  # 发送方 MAGI 身份（指向 magis_memberships.id）
    target_magi_id: int = 0  # 接收方 MAGI 身份（仅 target 可 claim）
    text: str = ""  # 请求正文
    deadline_at: datetime | None = None  # 调用方定义的业务截止时间


@dataclass(frozen=True, slots=True)
class A2ARequestResult(BaseJobResult):
    """Target MAGI 处理 :class:`A2ARequestJob` 后的回执。

    :attr:`JobStatus.COMPLETED` 表示 target 接受了请求并填了
    ``content`` 回传响应；:attr:`JobStatus.FAILED` 时
    ``error_code`` 是 :class:`A2AErrorCode` 中的稳定错误码
    （``TIMEOUT`` / 业务码），``error`` 是给人看的文案。
    """

    content: str = ""  # 目标 MAGI 回传的响应文本
    error_code: A2AErrorCode | None = None  # 稳定错误码（来自 A2AErrorCode）


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
    text: str = ""  # 通知正文


@dataclass(frozen=True, slots=True)
class A2ANotifyResult(BaseJobResult):
    """:class:`A2ANotifyJob` 的终端回执 — 仅在通知被消费且出错时落库。

    没有超时路径（notify 不阻塞发送方），也没有强制的 ``content``
    字段：:attr:`JobStatus.FAILED` 时 ``error_code`` 取自
    :class:`A2AErrorCode`，``error`` 描述投递失败原因，成功则
    通常只更新 ORM ``status`` 而不构造 Result。
    """

    error_code: A2AErrorCode | None = None  # 稳定错误码（来自 A2AErrorCode）


class _A2ARequestRow(BaseJobRowMixin):
    __tablename__ = "a2a_request_jobs"
    __table_args__ = (
        Index("ix_a2a_request_target_status", "target_magi_id", "status"),
        {"extend_existing": True},
    )

    source_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    target_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[A2AErrorCode | None] = mapped_column(
        enum_column(A2AErrorCode, name="a2a_error_code"),
        nullable=True,
        default=None,
    )
    #: 结果是否已被 source 通过 :meth:`get_result` 认领过。首次认领时写
    #: transcript，之后轮询不再重复写（替代旧的 dedup_key 幂等）。
    result_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class _A2ANotifyRow(BaseJobRowMixin):
    __tablename__ = "a2a_notify_jobs"
    __table_args__ = (
        Index("ix_a2a_notify_target_status", "target_magi_id", "status"),
        {"extend_existing": True},
    )

    source_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    target_magi_id: Mapped[int] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[A2AErrorCode | None] = mapped_column(
        enum_column(A2AErrorCode, name="a2a_error_code"),
        nullable=True,
        default=None,
    )


def _validate_route(
    memberships: MagisMembershipBook,
    *,
    source_magi_id: int,
    target_magi_id: int,
) -> None:
    """Reject A2A routes that no membership row can justify.

    Reads through :class:`~bus.firmwares.books.magis.membershipBook.MagisMembershipBook`
    rather than selecting ``magis_memberships`` directly, so guild
    boards never depend on another layer's private ORM row (same
    injection shape as
    :class:`~bus.firmwares.jobs.changeProviderConfigJob.changeProviderConfigJobBoard`
    and its ``settings_book``).

    The lookups run in the Book's own session, i.e. *before* the
    publishing transaction opens — a membership deleted in that
    window still fails the insert, because both ``source_magi_id``
    and ``target_magi_id`` are FKs onto ``magis_memberships.id``.
    This check exists to turn that ``IntegrityError`` into a
    readable error, and to enforce the one rule the FK cannot
    express: both ends live under the same MAGIS.
    """
    if source_magi_id <= 0 or target_magi_id <= 0:
        raise ValueError("source_magi_id and target_magi_id are required")
    if source_magi_id == target_magi_id:
        raise ValueError("A2A cannot target the sending MAGI")
    source = memberships.get(source_magi_id)
    target = memberships.get(target_magi_id)
    if source is None or target is None:
        raise LookupError("A2A source or target MAGI does not exist")
    if source.magis_id != target.magis_id:
        raise ValueError("A2A source and target must belong to the same MAGIS")


def _record_transcript(
    *,
    messages_book,
    conversations_book,
    peer_magi_id: int,
    job_id: int,
    event: str,
    role: AgentMessageRole | str,
    text: str,
) -> None:  # type: ignore[no-untyped-def]
    """Best-effort local transcript write for the MAGI executing an action.

    The A2A rows remain in the shared MAGIS database.  In contrast, both
    injected books belong to the caller's *local* Bus, so the same board code
    writes the source's transcript during publish/get-result and the target's
    transcript during claim/submit-result.  A failed transcript write must
    never undo an already committed A2A lifecycle transition.
    """
    if messages_book is None or conversations_book is None:
        return
    try:
        conversation = conversations_book.get_or_create_for_a2a_peer(
            peer_magi_id=peer_magi_id
        )
        messages_book.add(Message(
            conversation_id=conversation.id,
            role=AgentMessageRole(role) if isinstance(role, str) else role,
            text=text,
        ))
    except Exception:
        logger.exception("A2A transcript write failed: job=%s event=%s", job_id, event)


class a2aRequestJobBoard(BaseJobBoard[_A2ARequestRow, A2ARequestJob, A2ARequestResult]):
    """One request, one terminal response, claimed only by its target MAGI."""

    job_model = _A2ARequestRow
    job_cls = A2ARequestJob
    result_cls = A2ARequestResult

    def __init__(
        self,
        factory: EngineFactory,
        *,
        memberships_book: MagisMembershipBook,
        messages_book=None,  # type: ignore[no-untyped-def]
        conversations_book=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(factory)
        # Required, not optional: every publish must prove the route
        # exists and stays inside one MAGIS, so there is no degraded
        # "no book injected" mode to fall back to.
        self._memberships_book = memberships_book
        self._messages_book = messages_book
        self._conversations_book = conversations_book

    def _validate_publish(self, job: A2ARequestJob) -> None:
        """Prove the route exists and stays inside one MAGIS."""
        _validate_route(
            self._memberships_book,
            source_magi_id=job.source_magi_id,
            target_magi_id=job.target_magi_id,
        )

    def publish(self, job: A2ARequestJob) -> int:
        self._validate_publish(job)
        with self._session() as s:
            row = _A2ARequestRow(
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
                text=job.text,
                deadline_at=job.deadline_at,
            )
            s.add(row)
            s.commit()
            job_id = row.job_id
        _record_transcript(
            messages_book=self._messages_book,
            conversations_book=self._conversations_book,
            peer_magi_id=job.target_magi_id,
            job_id=job_id,
            event="publish",
            role="assistant",
            text=job.text,
        )
        return job_id

    def claim_for_target(
        self,
        *,
        magi_id: int,
        worker_id: str,
        active_source_magi_ids: set[int] | None = None,
    ) -> A2ARequestJob | None:
        with self._session() as s:
            extra_where = [_A2ARequestRow.target_magi_id == magi_id]
            if active_source_magi_ids:
                extra_where.append(_A2ARequestRow.source_magi_id.not_in(active_source_magi_ids))
            row = self._cas_claim(
                s,
                owner=self._require_worker_id(worker_id),
                extra_where=extra_where,
            )
            s.commit()
            job = self._map_row(row, A2ARequestJob) if row is not None else None
        if job is not None:
            _record_transcript(
                messages_book=self._messages_book,
                conversations_book=self._conversations_book,
                peer_magi_id=job.source_magi_id,
                job_id=job.job_id,
                event="claim",
                role="user",
                text=job.text,
            )
        return job

    def submit_result(self, *, job_id: int, worker_id: str, result: A2ARequestResult) -> None:
        """Complete a request only if this worker still owns its lease."""
        peer_magi_id: int | None = None
        with self._session() as s:
            row = s.scalar(select(_A2ARequestRow).where(_A2ARequestRow.job_id == job_id))
            if row is None:
                return
            submitted = self._submit(s, job_id=job_id, worker_id=self._require_worker_id(worker_id), result=result)
            s.commit()
            if submitted:
                peer_magi_id = row.source_magi_id
        if peer_magi_id is not None:
            _record_transcript(
                messages_book=self._messages_book,
                conversations_book=self._conversations_book,
                peer_magi_id=peer_magi_id,
                job_id=job_id,
                event="submit_result",
                role="assistant",
                text=result.content or result.error or "",
            )

    def get_result(self, *, job_id: int) -> A2ARequestResult | None:
        peer_magi_id: int | None = None
        first_claim = False
        with self._session() as s:
            row = s.scalar(select(_A2ARequestRow).where(_A2ARequestRow.job_id == job_id))
            if row is None:
                return None
            if row.status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                s.commit()
                return None
            result = self._read_result_from_job(row)
            # 首次认领 result 时置位并写 transcript；之后轮询只返回结果不再写
            # （替代旧的 dedup_key 幂等 —— 见 _A2ARequestRow.result_claimed）。
            claimed = s.execute(
                update(_A2ARequestRow)
                .where(
                    _A2ARequestRow.job_id == job_id,
                    _A2ARequestRow.result_claimed.is_(False),
                )
                .values(result_claimed=True)
            )
            first_claim = getattr(claimed, "rowcount", 0) == 1
            s.commit()
            peer_magi_id = row.target_magi_id
        if first_claim:
            _record_transcript(
                messages_book=self._messages_book,
                conversations_book=self._conversations_book,
                peer_magi_id=peer_magi_id,
                job_id=job_id,
                event="get_result",
                role="user",
                text=result.content or result.error or "",
            )
        return result

class a2aNotifyBoard(BaseJobBoard[_A2ANotifyRow, A2ANotifyJob, A2ANotifyResult]):
    """Reliable one-way notification; publishers never wait for its result."""

    job_model = _A2ANotifyRow
    job_cls = A2ANotifyJob
    result_cls = A2ANotifyResult

    def __init__(
        self,
        factory: EngineFactory,
        *,
        memberships_book: MagisMembershipBook,
        messages_book=None,  # type: ignore[no-untyped-def]
        conversations_book=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(factory)
        self._memberships_book = memberships_book
        self._messages_book = messages_book
        self._conversations_book = conversations_book

    def _validate_publish(self, job: A2ANotifyJob) -> None:
        """Prove the route exists and stays inside one MAGIS."""
        _validate_route(
            self._memberships_book,
            source_magi_id=job.source_magi_id,
            target_magi_id=job.target_magi_id,
        )

    def publish(self, job: A2ANotifyJob) -> int:
        self._validate_publish(job)
        with self._session() as s:
            row = _A2ANotifyRow(
                source_magi_id=job.source_magi_id,
                target_magi_id=job.target_magi_id,
                text=job.text,
            )
            s.add(row)
            s.commit()
            job_id = row.job_id
        _record_transcript(
            messages_book=self._messages_book,
            conversations_book=self._conversations_book,
            peer_magi_id=job.target_magi_id,
            job_id=job_id,
            event="publish",
            role="assistant",
            text=job.text,
        )
        return job_id

    def claim_for_target(
        self,
        *,
        magi_id: int,
        worker_id: str,
        active_source_magi_ids: set[int] | None = None,
    ) -> A2ANotifyJob | None:
        with self._session() as s:
            extra_where = [_A2ANotifyRow.target_magi_id == magi_id]
            if active_source_magi_ids:
                extra_where.append(_A2ANotifyRow.source_magi_id.not_in(active_source_magi_ids))
            row = self._cas_claim(
                s,
                owner=self._require_worker_id(worker_id),
                extra_where=extra_where,
            )
            s.commit()
            job = self._map_row(row, A2ANotifyJob) if row is not None else None
        if job is not None:
            _record_transcript(
                messages_book=self._messages_book,
                conversations_book=self._conversations_book,
                peer_magi_id=job.source_magi_id,
                job_id=job.job_id,
                event="claim",
                role="user",
                text=job.text,
            )
        return job


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
