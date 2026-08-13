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
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin

if TYPE_CHECKING:
    from magi.bus.library.local.contactBook import ContactBook

logger = logging.getLogger(__name__)

# =========================================================================
# chatJobBoard — durable agent turn queue (chat_jobs table)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ChatJob(BaseJob):
    """Snapshot of a turn request (publisher input).

    Typed fields, no ``payload`` dict. The DB row still stores
    these in a JSON ``payload`` column (see
    :meth:`to_payload` / :meth:`from_payload`) so the schema
    doesn't change for now — but at the Python API surface
    producers and consumers see one attribute per field, not a
    black-box dict.

    Core turn input (set by :meth:`chatJobBoard.publish`):

    - :attr:`text` — the user message (raw, pre-cap; the
      ``chat_messages`` row carries the post-cap version).
    - :attr:`channel` — the inbound channel: ``"tg"`` / ``"webui"``
      / ``"task"`` / etc.
    - :attr:`contact_id` — owning contact; ``None`` for task-driven
      publishes with no contact.

    ``caller_role`` is intentionally **not** carried here — the agent
    worker resolves it from :meth:`ContactBook.get` at claim time
    (a live value, not a publish-time snapshot that can go stale).
    Channel-specific fields (``chat_id`` / ``tg_message_id`` /
    ``kind`` / ``task_id`` / ``manual``) were removed too: the agent
    never reads them — the reply address lives on the conversation
    row's ``delivery_address``, not on the turn.
    """

    conversation_id: str | None = None  # 会话 ID（WebUI 多会话 / TG 单会话）
    # Core turn input
    text: str = ""  # 用户消息原文（pre-cap，chat_messages 存截断后）
    channel: str = ""  # 入站渠道：tg / webui / task / ...
    contact_id: int | None = None  # 所属联系人；task 无联系人时为 None


class ChatErrorCode(StrEnum):
    """Stable error code returned on a failed :class:`ChatJobResult`.

    ``StrEnum`` so each member is a ``str`` subclass — ``==`` against
    string literals, JSON serialisation and any ``String`` columns keep
    working unchanged. Mirrors
    :class:`magi.bus.guild.callLLMJob.LLMErrorCode` and
    :class:`magi.bus.guild.a2aJob.A2AErrorCode`.
    """

    RUN_CANCELLED = "magi.run_cancelled"  # 运行被取消（cancel_event / 关闭）
    AGENT_CRASHED = "agent_crashed"  # agent loop 未捕获异常
    LLM_TIMEOUT = "llm_timeout"  # LLM 调用超时（result is None）
    LLM_FAILED = "llm_failed"  # LLM 返回非 COMPLETED（细节见 BaseJobResult.error）
    LEASE_LOST = "lease_lost"  # 工具/A2A 汇聚阶段 lease 丢失


@dataclass(frozen=True, slots=True)
class ChatJobResult(BaseJobResult):
    """Final state of a turn."""

    result: dict[str, Any] | None = None  # 结构化结果
    error_code: ChatErrorCode | None = None  # 稳定错误码（失败时非 None）
    # 失败的人类可读文案用继承的 ``BaseJobResult.error``，不再另设 error_detail


class _ChatJobRow(BaseJobRowMixin):
    __tablename__ = "chat_jobs"
    __table_args__ = {"extend_existing": True}

    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 会话 ID
    # Turn input — formerly a single ``payload`` JSON blob. Split into
    # individual columns in migration 0011 so producers / consumers
    # see one field per attribute on :class:`ChatJob` (no
    # ``payload`` dict). The pre-migration rows had no value here.
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 用户消息原文
    channel: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # 入站渠道
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 所属联系人


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
        messages_book=None,  # type: ignore[no-untyped-def]
        conversations_book=None,  # type: ignore[no-untyped-def]
        lease_seconds: int = 300,
    ) -> None:
        super().__init__(factory, lease_seconds=lease_seconds)
        # ``contact_book`` is optional so unit tests can build a board
        # without the local contacts store; in that case the
        # ``last_seen_at`` stamp is silently skipped.
        self._contact_book = contact_book
        # ``messages_book`` and ``conversations_book`` are optional
        # so existing tests can build a board with just a factory.
        # In production, :func:`magi.bus.bootstrap.open_bus` wires
        # both — :meth:`publish` uses them to (a) enforce the
        # D.22 cross-channel guard and (b) persist the user message
        # to ``chat_messages`` at the same chokepoint as the chatJob
        # enqueue, so callers don't reach into the messages Book
        # directly. The cap is enforced by :meth:`MessageBook.add`
        # (the persistent layer is what compaction reads), not here.
        self._messages_book = messages_book
        self._conversations_book = conversations_book

    def publish(self, job: ChatJob, *, message_id: str | None = None) -> str:
        """Enqueue one agent turn and persist the user message.

        Single chokepoint for inbound turn intake — every path that
        enqueues a turn (channel intake *and* internal steering
        republishes) goes through here:

          1. D.22 cross-channel guard — refuses the publish if the
             conversation exists and was created on a different
             channel (raises :class:`ChannelMismatchError`). Reads the
             typed ``contact_id`` / ``channel`` / ``conversation_id``
             fields on the ChatJob, so any caller gets the same
             protection. Skipped when ``contact_id is None`` (task
             path with no contact), no ``conversations_book`` (legacy
             / tests), or no channel (misconfigured job).
          2. Enqueue the chatJob row.
          3. Stamp ``contacts.last_seen_at`` (best-effort — a failure
             is logged and swallowed so a transient ``contact_book``
             outage cannot block an inbound turn).
          4. Persist the user message to ``chat_messages`` (the same
             row the agent's LLM call reads via
             :func:`build_messages_from_conversation`). ``MessageBook.add``
             enforces the inbound cap; the chatJob row carries the raw
             text. Skipped when the board has no ``messages_book``
             (legacy / tests) — best-effort, since the chatJob is
             already enqueued.

        The per-turn text cap is **not** applied to the chatJob row —
        that lives in :meth:`MessageBook.add`, which is the chokepoint
        compaction reads.

        ``message_id`` is the ULID to use for the ``chat_messages``
        row — pass the same value on retry for producer-side idempotency.
        ``job_id`` is **always Board-generated** (see
        :meth:`BaseJobBoard.publish`); callers can't pass one in.

        Returns the *job_id* of the published job (Board-generated).
        """
        contact_id = job.contact_id
        channel = job.channel
        conversation_id = job.conversation_id or ""
        if (
            contact_id is not None
            and self._conversations_book is not None
            and channel
        ):
            try:
                cid_int = int(contact_id)
            except (TypeError, ValueError):
                cid_int = None
            if cid_int is not None:
                conversation = self._conversations_book.get_for_owner(
                    contact_id=cid_int, conversation_id=conversation_id
                )
                if (
                    conversation is not None
                    and conversation.channel != channel
                ):
                    from magi.bus.library.local.conversationBook import (
                        ChannelMismatchError,
                    )

                    raise ChannelMismatchError(conversation.channel)
        with self._session() as s:
            row = self._build_pending_row(job)
            s.add(row)
            s.flush()
            s.commit()
            job_id = row.job_id
        self._stamp_last_seen(job)
        if self._messages_book is not None:
            try:
                self._messages_book.add(
                    conversation_id=conversation_id,
                    role="user",
                    text=job.text,
                    message_id=message_id,
                )
            except Exception:
                logger.exception(
                    "chatJobBoard.publish: messages_book.add failed "
                    "(conversation=%s, channel=%s); chatJob %s enqueued without row",
                    conversation_id,
                    channel,
                    job_id,
                )
        return job_id

    def _stamp_last_seen(self, job: ChatJob) -> None:
        """Best-effort ``last_seen_at`` update keyed on ``job.contact_id``.

        No-op when the board was constructed without a
        ``contact_book`` (test mode) or when the job has no
        contact (e.g. an internal agent-side republish). Runs in
        its own transaction, isolated from the chatJob insert
        that already committed.
        """
        if self._contact_book is None:
            return
        if job.contact_id is None:
            return
        try:
            self._contact_book.touch(contact_id=job.contact_id)
        except Exception:
            logger.exception(
                "chatJobBoard.publish: contact_book.touch failed for contact_id=%r", job.contact_id
            )

    def claim_for_steering(self, *, conversation_id: str) -> ChatJob | None:
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
            return self._map_row(row, ChatJob)


__all__ = [
    "ChatErrorCode",
    "ChatJob",
    "ChatJobResult",
    "chatJobBoard",
    "_ChatJobRow",
]
