"""chatJobBoard — durable agent turn queue.

Backed by the ``chat_jobs`` table.  A publish inserts a new row;
a claim picks up the oldest pending row, updates its ``status`` and
lease fields, and returns the job snapshot.  Submitting the result
moves the row's ``status`` to ``completed``/``failed``.

As a side effect of enqueue, :meth:`chatJobBoard.publish` also
stamps ``contacts.last_seen_at`` so the directory's recency
ordering (:meth:`ContactBook.search`) reflects real inbound
traffic — every code path that enqueues a turn, including
direct :meth:`publish` callers, picks this up automatically.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, _row_to_job, new_job_id

if TYPE_CHECKING:
    from magi.bus.library.local.contactBook import ContactBook

logger = logging.getLogger(__name__)

# =========================================================================
# chatJobBoard — durable agent turn queue (chat_jobs table)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ChatJob:
    """Snapshot of a turn request (publisher input)."""

    job_id: str = ""  # 发布时自动生成的 job_id
    conversation_id: str | None = None  # 目标 chat 会话 ID
    correlation_id: str | None = None  # 跨系统追踪 ID
    payload: dict[str, Any] | None = None  # turn 输入（text/channel/contact_id/...）
    available_at: datetime | None = None  # 最早可被 claim 的时间
    received_seq: int = 0  # 会话内的接收序号（用于保持 turn 顺序）


@dataclass(frozen=True, slots=True)
class ChatJobResult:
    """Final state of a turn."""

    job_id: str = ""  # 对应 ChatJob 的 job_id
    success: bool = False  # turn 是否成功完成
    status: str = "failed"  # 终态（completed/failed）
    result: dict[str, Any] | None = None  # 结构化结果
    error_code: str | None = None  # 稳定错误码
    error_detail: str | None = None  # 失败时的详细错误描述


class _ChatJobRow(Base):
    __tablename__ = "chat_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    received_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class chatJobBoard(BaseJobBoard[_ChatJobRow, ChatJob, ChatJobResult]):
    """Queue (write + claim + submit_result) for agent turns."""

    job_model = _ChatJobRow
    job_cls = ChatJob
    result_cls = ChatJobResult
    natural_key_attr = "job_id"

    def __init__(
        self,
        factory,  # type: ignore[no-untyped-def]
        *,
        contact_book: ContactBook | None = None,
        lease_seconds: int = 300,
    ) -> None:
        super().__init__(factory, lease_seconds=lease_seconds)
        # ``contact_book`` is optional so unit tests can build a board
        # without the local contacts store; in that case the
        # ``last_seen_at`` stamp is silently skipped.
        self._contact_book = contact_book

    def _insert_pending(self, session, job: ChatJob, **_kwargs) -> _ChatJobRow:
        job_id = job.job_id or new_job_id()
        row = _ChatJobRow(
            job_id=job_id,
            conversation_id=job.conversation_id,
            correlation_id=job.correlation_id,
            payload=job.payload,
            received_seq=job.received_seq,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row

    def publish(self, job: ChatJob) -> str:
        """Enqueue one agent turn and stamp ``last_seen_at`` for the contact.

        The DB insert runs first; only after the ChatJob row is
        durable do we update the contacts table. The activity
        stamp is best-effort — a failure is logged and swallowed
        so a transient ``contact_book`` outage cannot block an
        inbound turn. Reading ``contact_id`` from ``job.payload``
        (rather than a separate argument) means any caller of
        ``publish`` — :meth:`publish_chat`, future internal
        steering republishes, dead-code agent producers — gets
        the stamp uniformly.
        """
        with self._session() as s:
            row = self._insert_pending(s, job)
            s.commit()
            job_id = row.job_id
        self._stamp_last_seen(job)
        return job_id

    def publish_chat(
        self,
        *,
        text: str,
        channel: str,
        contact_id: int | None,
        conversation_id: str,
        caller_role: str | None = None,
        job_id: str | None = None,
        correlation_id: str | None = None,
        **extras: Any,
    ) -> str:
        """Channel→agent convenience: build a ChatJob from channel args and enqueue.

        All channel workers share the same core payload keys
        (``text`` / ``channel`` / ``contact_id`` /
        ``conversation_id`` / ``caller_role``). Channel-specific
        extras (``chat_id`` / ``task_id`` / ``manual`` / ...)
        are forwarded as additional payload keys via ``**extras``.

        ``job_id`` and ``correlation_id`` are for callers that
        need stable idempotency keys (e.g. WebUI). When
        ``job_id`` is omitted the format is
        ``"{channel}:{uuid16}"``.

        ``contact_id`` is ``int | None`` because
        :class:`magi.bus.library.local.tasksBook.Task`-driven
        publishes can fire for a task with no bound contact; in
        that case the ChatJob is still enqueued but
        ``last_seen_at`` is left alone (no contact to stamp).

        Returns the *job_id* of the published job.
        """
        resolved_job_id = job_id or f"{channel}:{uuid.uuid4().hex[:16]}"
        payload: dict[str, Any] = {
            "text": text,
            "channel": channel,
            "contact_id": contact_id,
            "conversation_id": conversation_id,
        }
        if caller_role is not None:
            payload["caller_role"] = caller_role
        payload.update(extras)
        job = ChatJob(
            job_id=str(resolved_job_id),
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            payload=payload,
        )
        return self.publish(job)

    def _stamp_last_seen(self, job: ChatJob) -> None:
        """Best-effort ``last_seen_at`` update keyed on ``job.payload['contact_id']``.

        No-op when the board was constructed without a
        ``contact_book`` (test mode) or when the payload lacks a
        ``contact_id`` key (e.g. an internal agent-side
        republish). Runs in its own transaction, isolated from
        the chatJob insert that already committed.
        """
        if self._contact_book is None:
            return
        contact_id_raw = (job.payload or {}).get("contact_id")
        try:
            contact_id = int(contact_id_raw) if contact_id_raw is not None else None
        except (TypeError, ValueError):
            contact_id = None
        if contact_id is None:
            return
        try:
            self._contact_book.touch(contact_id=contact_id)
        except Exception:
            logger.exception(
                "chatJobBoard.publish: contact_book.touch failed for contact_id=%r", contact_id
            )

    def claim_for_conversation(self, *, conversation_id: str) -> ChatJob | None:
        """CAS-claim a ChatJob scoped to one conversation.

        设计 §2.5 + §5.2：AgentWorker 在 ``_gather_all`` 中每轮轮询调用，
        认领同 conversation 的 pending ChatJob 作为 steering。steering
        只取消息、不动 conversation 状态（lease 由 AgentWorker 自身管理）。

        Thin wrapper around :meth:`BaseJobBoard._cas_claim` —
        passes ``conversation_id=...`` as the extra WHERE so the
        candidate pool is scoped to one conversation. The CAS
        pattern (find candidate → conditional UPDATE → check
        rowcount) replaces the previous ``SELECT ... FOR UPDATE
        SKIP LOCKED`` which SQLite silently no-ops under WAL.
        """
        with self._session() as s:
            row = self._cas_claim(
                s,
                owner=f"steer:{conversation_id}:{id(self)}",
                extra_where=[_ChatJobRow.conversation_id == conversation_id],
            )
            s.commit()
            if row is None:
                return None
            return _row_to_job(row, ChatJob)
            return None


__all__ = [
    "ChatJob",
    "ChatJobResult",
    "chatJobBoard",
    "_ChatJobRow",
]
