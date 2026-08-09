"""chatJobBoard — durable agent turn queue.

Backed by the ``chat_jobs`` table.  A publish inserts a new row;
a claim picks up the oldest pending row, updates its ``status`` and
lease fields, and returns the job snapshot.  Submitting the result
moves the row's ``status`` to ``completed``/``failed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, and_, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, _row_to_job, new_job_id


# =========================================================================
# chatJobBoard — durable agent turn queue (chat_jobs table)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ChatJob:
    """Snapshot of a turn request (publisher input)."""

    event_id: str = ""
    run_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None
    kind: str = "chat"
    payload: dict[str, Any] | None = None
    inbox_event_id: str | None = None
    available_at: datetime | None = None
    received_seq: int = 0


@dataclass(frozen=True, slots=True)
class ChatJobResult:
    """Final state of a turn."""

    event_id: str = ""
    success: bool = False
    status: str = "failed"
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None


class _ChatJobRow(Base):
    __tablename__ = "chat_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    inbox_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    received_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class chatJobBoard(BaseJobBoard[_ChatJobRow, ChatJob, ChatJobResult]):
    """Queue (write + claim + submit_result) for agent turns."""

    job_model = _ChatJobRow
    job_cls = ChatJob
    result_cls = ChatJobResult
    natural_key_attr = "event_id"

    def _insert_pending(self, session, job: ChatJob, **kwargs) -> _ChatJobRow:
        event_id = job.event_id or new_job_id()
        row = _ChatJobRow(
            event_id=event_id,
            run_id=job.run_id,
            conversation_id=job.conversation_id,
            correlation_id=job.correlation_id,
            inbox_event_id=job.inbox_event_id,
            kind=job.kind or "chat",
            payload=job.payload,
            received_seq=job.received_seq,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row

    def publish(self, job: ChatJob) -> str:
        """发布 agent turn 请求，返回 event_id。"""
        with self._session() as s:
            row = self._insert_pending(s, job)
            s.commit()
            return row.event_id

    def claim_for_conversation(self, *, conversation_id: str) -> ChatJob | None:
        """[claude, 2026-08-08] CAS-claim a ChatJob scoped to one conversation.

        设计 §2.5 + §5.2：AgentWorker 在 ``_gather_all`` 中每轮轮询调用，
        认领同 conversation 的 pending ChatJob 作为 steering。steering
        只取消息、不动 conversation 状态（lease 由 AgentWorker 自身管理）。

        为什么不用 ``SELECT ... FOR UPDATE SKIP LOCKED``：

        - SQLite 的 ``SKIP LOCKED`` 在 WAL 模式下不提供严格互斥语义
          （其他 writer 仍可读到同一行）；设计 §2.5 明确禁止。
        - 我们要的是"如果另一个 worker 已经拿到这一行，我就让出"，
          不是"如果行被锁，我就跳到下一行"。
        - CAS UPDATE 让 SQLite 用一次原子写就完成 "存在 + 状态匹配"
          的判断，rowcount 直接告诉我们是否抢到。

        流程：

        1. ``SELECT id`` 找候选（最旧 pending 或 leased-过期 processing，
           同 conversation_id，按 created_at + id 排序）；
        2. 对候选 ``UPDATE SET status='processing', leased_by=:owner,
           leased_until=:now+lease, attempts=attempts+1 WHERE
           conversation_id=:cid AND id=:id AND (status='pending' OR
           (status='processing' AND leased_until < :now))``；
        3. rowcount == 1 → 拿到；rowcount == 0 → 重选下一个候选。

        重试上限 = MAX_ATTEMPTS_CANDIDATES（10）防止极端 hot conversation
        死循环；abort 时返回 None。
        """
        MAX_ATTEMPTS_CANDIDATES = 10
        owner = f"steer:{conversation_id}:{id(self)}"
        with self._session() as s:
            now = utcnow_naive()
            lease_until = now + timedelta(seconds=self._lease_seconds)
            for _ in range(MAX_ATTEMPTS_CANDIDATES):
                # 1. find candidate (no lock)
                row = s.scalar(
                    select(_ChatJobRow)
                    .where(
                        _ChatJobRow.conversation_id == conversation_id,
                        or_(
                            _ChatJobRow.status == "pending",
                            and_(
                                _ChatJobRow.status == "processing",
                                _ChatJobRow.leased_until < now,
                            ),
                        ),
                    )
                    .order_by(_ChatJobRow.created_at, _ChatJobRow.id)
                    .limit(1)
                )
                if row is None:
                    return None
                # 2. CAS UPDATE — 行级原子写
                from sqlalchemy import update

                result = s.execute(
                    update(_ChatJobRow)
                    .where(
                        _ChatJobRow.id == row.id,
                        _ChatJobRow.conversation_id == conversation_id,
                        or_(
                            _ChatJobRow.status == "pending",
                            and_(
                                _ChatJobRow.status == "processing",
                                _ChatJobRow.leased_until < now,
                            ),
                        ),
                    )
                    .values(
                        status="processing",
                        leased_by=owner,
                        leased_until=lease_until,
                        attempts=_ChatJobRow.attempts + 1,
                        started_at=now,
                    )
                )
                if result.rowcount == 1:
                    s.commit()
                    # reload fresh row to return
                    fresh = s.get(_ChatJobRow, row.id)
                    return _row_to_job(fresh, ChatJob)  # type: ignore[arg-type]
                # 3. lost the race — try next candidate
                s.rollback()
                now = utcnow_naive()
                lease_until = now + timedelta(seconds=self._lease_seconds)
            return None


def publish_chat(
    bus,  # type: ignore[no-untyped-def]
    *,
    text: str,
    channel: str,
    uid: int,
    session_id: str,
    kind: str = "chat",
    caller_role: str | None = None,
    event_id: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    correlation_id: str | None = None,
    **extras: Any,
) -> str:
    """Publish a ChatJob with the common channel→agent payload pattern.

    All channel workers share the same core payload keys (text,
    channel, uid, session_id, caller_role).  Channel-specific extras
    (tg_chat_id, task_id, fired_by, etc.) are forwarded as additional
    payload keys via **extras.

    *event_id*, *run_id*, *conversation_id*, and *correlation_id*
    are for callers that need stable idempotency keys (e.g. WebUI).

    Returns the *event_id* of the published job.
    """
    import uuid
    if event_id is None:
        event_id = f"{channel}:{uuid.uuid4().hex[:16]}"
    payload: dict[str, Any] = {
        "text": text,
        "channel": channel,
        "uid": uid,
        "session_id": session_id,
    }
    if caller_role is not None:
        payload["caller_role"] = caller_role
    payload.update(extras)

    job = ChatJob(
        event_id=str(event_id),
        run_id=run_id or "",
        conversation_id=conversation_id,
        correlation_id=correlation_id,
        kind=kind,
        payload=payload,
    )
    bus.agent_job_board.publish(job)
    return str(event_id)


__all__ = [
    "ChatJob",
    "ChatJobResult",
    "chatJobBoard",
    "publish_chat",
    "_ChatJobRow",
]
