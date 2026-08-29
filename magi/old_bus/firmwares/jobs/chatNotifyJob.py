"""chatNotifyBoard — durable agent turn queue.

Backed by the ``chat_notify_jobs`` table.  A publish inserts a new row;
a claim picks up the oldest pending row, updates its ``status`` and
lease fields, and returns the job snapshot.  Submitting the result
moves the row's ``status`` to ``completed``/``failed``.

As a side effect of enqueue, :meth:`chatNotifyBoard.publish` also
stamps ``contacts.last_seen_at`` so the directory's recency
ordering (:meth:`ContactBook.search`) reflects real inbound
traffic — every code path that enqueues a turn, including
direct :meth:`publish` callers, picks this up automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Integer, Text, or_
from sqlalchemy.orm import Mapped, mapped_column

from magi.old_bus.bases.job import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin

if TYPE_CHECKING:
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

logger = logging.getLogger(__name__)

# =========================================================================
# chatNotifyBoard — durable agent turn queue (chat_notify_jobs table)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ChatNotifyJob(BaseJob):
    """Snapshot of a turn request (publisher input).

    Typed fields, no ``payload`` dict. The DB row still stores
    these in a JSON ``payload`` column (see
    :meth:`to_payload` / :meth:`from_payload`) so the schema
    doesn't change for now — but at the Python API surface
    producers and consumers see one attribute per field, not a
    black-box dict.

    Core turn input (set by :meth:`chatNotifyBoard.publish`):

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

    conversation_id: int | None = None  # 会话 ID（chat_conversations.id；WebUI 多会话 / TG 单会话）
    # Core turn input
    text: str = ""  # 用户消息原文（pre-cap，chat_messages 存截断后）
    channel: str = ""  # 入站渠道：tg / webui / task / ...
    contact_id: int | None = None  # 所属联系人；task 无联系人时为 None


@dataclass(frozen=True, slots=True)
class ChatNotifyResult(BaseJobResult):
    """Final state of a turn.

    Channel workers (TG / WebUI / …) only read ``status`` (see
    :class:`JobStatus`) — they never see a stable error code. When the
    agent loop fails, :meth:`AgentWorker._publish_delivery` enqueues a
    :class:`DeliveryNotifyJob` carrying the user-facing error text, and
    ``chat_notify_jobs.status`` flips to ``FAILED``. Operators diagnose
    failures from the inherited :attr:`BaseJobResult.error` (humman-
    readable string) plus stderr; the structured
    ``LLMErrorCode`` / ``A2AErrorCode`` layers stay on the provider /
    A2A board paths where the corresponding workers live.
    """


class _ChatNotifyRow(BaseJobRowMixin):
    __tablename__ = "chat_notify_jobs"
    __table_args__ = {"extend_existing": True}

    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 会话 ID
    # Turn input — formerly a single ``payload`` JSON blob. Split into
    # individual columns in migration 0011 so producers / consumers
    # see one field per attribute on :class:`ChatNotifyJob` (no
    # ``payload`` dict). The pre-migration rows had no value here.
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 用户消息原文
    channel: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 入站渠道
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 所属联系人


class chatNotifyBoard(BaseJobBoard[_ChatNotifyRow, ChatNotifyJob, ChatNotifyResult]):
    """Queue (write + claim + submit_result) for agent turns."""

    job_model = _ChatNotifyRow
    job_cls = ChatNotifyJob
    result_cls = ChatNotifyResult

    # chatNotify 的 lease 比通用 60s 长: 单次 agent turn 通常要串多个
    # LLM / 工具调用, 60s 内不一定完成 submit_result —— 提早被另一个
    # worker 抢走会出现「同一轮对话被并发推进」的奇怪现象。
    _lease_seconds = 300

    def __init__(
        self,
        factory,  # type: ignore[no-untyped-def]
        *,
        contact_book: ContactBook | None = None,
        messages_book=None,  # type: ignore[no-untyped-def]
        conversations_book=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(factory)
        # ``contact_book`` is optional so unit tests can build a board
        # without the local contacts store; in that case the
        # ``last_seen_at`` stamp is silently skipped.
        self._contact_book = contact_book
        # ``messages_book`` and ``conversations_book`` are optional
        # so existing tests can build a board with just a factory.
        # In production, :func:`magi.bus.bootstrap.open_bus` wires
        # both — :meth:`publish` uses them to (a) enforce the
        # D.22 cross-channel guard and (b) persist the user message
        # to ``chat_messages`` at the same chokepoint as the chatNotifyJob
        # enqueue, so callers don't reach into the messages Book
        # directly. The cap is enforced by :meth:`MessageBook.add`
        # (the persistent layer is what compaction reads), not here.
        self._messages_book = messages_book
        self._conversations_book = conversations_book

    def publish(self, job: ChatNotifyJob) -> int:
        """Enqueue one agent turn and persist the user message.

        Single chokepoint for inbound turn intake — every path that
        enqueues a turn (channel intake *and* internal steering
        republishes) goes through here:

          1. D.22 cross-channel guard — see :meth:`_validate_publish`.
             Refuses the publish if the conversation exists and was
             created on a different channel.
          2. Enqueue the chatNotifyJob row.
          3. Stamp ``contacts.last_seen_at`` (best-effort — a failure
             is logged and swallowed so a transient ``contact_book``
             outage cannot block an inbound turn).
          4. Persist the user message to ``chat_messages`` (the same
             row the agent's LLM call reads via
             :func:`build_messages_from_conversation`). ``MessageBook.add``
             enforces the inbound cap; the chatNotifyJob row carries the raw
             text. Skipped when the board has no ``messages_book``
             (legacy / tests) — best-effort, since the chatNotifyJob is
             already enqueued.

        The per-turn text cap is **not** applied to the chatNotifyJob row —
        that lives in :meth:`MessageBook.add`, which is the chokepoint
        compaction reads.

        ``job_id`` is **always Board-generated** (see
        :meth:`BaseJobBoard.publish`); callers can't pass one in.

        Returns the *job_id* of the published job (Board-generated).
        """
        self._validate_publish(job)
        channel = job.channel
        conversation_id = job.conversation_id or 0
        with self._session() as s:
            row = self._build_pending_row(job)
            s.add(row)
            s.flush()
            s.commit()
            job_id = row.job_id
        self._stamp_last_seen(job)
        if self._messages_book is not None:
            try:
                from magi.old_bus.firmwares.books.local.conversationBook import (
                    AgentMessageRole,
                    Message,
                )

                self._messages_book.add(Message(
                    conversation_id=conversation_id,
                    role=AgentMessageRole.USER,
                    text=job.text,
                ))
            except Exception:
                logger.exception(
                    "chatNotifyBoard.publish: messages_book.add failed "
                    "(conversation=%s, channel=%s); chatNotifyJob %s enqueued without row",
                    conversation_id,
                    channel,
                    job_id,
                )
        return job_id

    def claim_for_new_conversation(
        self,
        *,
        worker_id: str,
        active_conversation_ids: set[int],
    ) -> ChatNotifyJob | None:
        """Claim a turn whose conversation is not locally active.

        Agent workers may process different conversations concurrently, but a
        second top-level turn for an active conversation must remain pending:
        the active run consumes it through :meth:`claim_for_steering` while it
        waits for tools or A2A work.  Applying this filter in the CAS claim
        (rather than claiming and releasing) preserves that ordering without
        lease churn.
        """
        worker_id = self._require_worker_id(worker_id)
        with self._session() as session:
            extra_where = None
            if active_conversation_ids:
                extra_where = [
                    or_(
                        self.job_model.conversation_id.is_(None),
                        self.job_model.conversation_id.not_in(active_conversation_ids),
                    )
                ]
            row = self._cas_claim(session, owner=worker_id, extra_where=extra_where)
            session.commit()
            return self._map_row(row, self.job_cls) if row else None

    def _validate_publish(self, job: ChatNotifyJob) -> None:
        """D.22 cross-channel guard.

        Refuses the publish if the conversation exists and was created
        on a different channel (raises :class:`ChannelMismatchError`).
        Skipped when ``contact_id`` is None (task path with no contact),
        there is no ``conversations_book`` (legacy / tests), or the job
        carries no ``channel`` (misconfigured).
        """
        contact_id = job.contact_id
        channel = job.channel
        if contact_id is None or self._conversations_book is None or not channel:
            return
        try:
            cid_int = int(contact_id)
        except (TypeError, ValueError):
            return
        conversation = self._conversations_book.get_for_owner(
            contact_id=cid_int, conversation_id=job.conversation_id or 0
        )
        if conversation is not None and conversation.channel != channel:
            from magi.old_bus.firmwares.books.local.conversationBook import ChannelMismatchError

            raise ChannelMismatchError(conversation.channel)

    def _stamp_last_seen(self, job: ChatNotifyJob) -> None:
        """Best-effort ``last_seen_at`` update keyed on ``job.contact_id``.

        No-op when the board was constructed without a
        ``contact_book`` (test mode) or when the job has no
        contact (e.g. an internal agent-side republish). Runs in
        its own transaction, isolated from the chatNotifyJob insert
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
                "chatNotifyBoard.publish: contact_book.touch failed for contact_id=%r", job.contact_id
            )

    def claim_for_steering(
        self,
        *,
        conversation_id: int,
        worker_id: str,
    ) -> ChatNotifyJob | None:
        """CAS-claim a ChatNotifyJob scoped to one conversation.

        设计 §2.5 + §5.2：AgentWorker 在 ``_gather_all`` 中每轮轮询调用，
        认领同 conversation 的 pending ChatNotifyJob 作为 steering。steering
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
                owner=self._require_worker_id(worker_id),
                extra_where=[_ChatNotifyRow.conversation_id == conversation_id],
            )
            s.commit()
            if row is None:
                return None
            return self._map_row(row, ChatNotifyJob)


__all__ = [
    "ChatNotifyJob",
    "ChatNotifyResult",
    "chatNotifyBoard",
    "_ChatNotifyRow",
]
