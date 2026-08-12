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

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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
    """Snapshot of a turn request (publisher input).

    Typed fields, no ``payload`` dict. The DB row still stores
    these in a JSON ``payload`` column (see
    :meth:`to_payload` / :meth:`from_payload`) so the schema
    doesn't change for now — but at the Python API surface
    producers and consumers see one attribute per field, not a
    black-box dict.

    Core turn input (set by :meth:`chatJobBoard.publish_chat`):

    - :attr:`text` — the user message (raw, pre-cap; the
      ``chat_messages`` row carries the post-cap version).
    - :attr:`channel` — the inbound channel: ``"tg"`` / ``"webui"``
      / ``"task"`` / etc.
    - :attr:`contact_id` — owning contact; ``None`` for task-driven
      publishes with no contact.
    - :attr:`caller_role` — the contact's role at publish time
      (admin / guest / assigned); ``None`` if unknown.

    Channel-specific (all optional, all ``None`` unless the matching
    channel sets them):

    - :attr:`chat_id` — TG: the ``tgid``.
    - :attr:`tg_message_id` — TG: the upstream Telegram message id.
    - :attr:`kind` — Task: a tag like ``"task.triggered"``.
    - :attr:`task_id` — Task: the source task id.
    - :attr:`manual` — Task: whether the fire was manual.
    """

    job_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None
    # Core turn input
    text: str = ""
    channel: str = ""
    contact_id: int | None = None
    caller_role: str | None = None
    # Channel-specific
    chat_id: str | None = None
    tg_message_id: int | None = None
    kind: str | None = None
    task_id: str | None = None
    manual: bool | None = None
    # Queue control
    available_at: datetime | None = None
    received_seq: int = 0


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
    # Turn input — formerly a single ``payload`` JSON blob. Split into
    # individual columns in migration 0011 so producers / consumers
    # see one field per attribute on :class:`ChatJob` (no
    # ``payload`` dict). The pre-migration rows had no value here.
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caller_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Channel-specific (nullable; only the matching channel sets them)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Queue control
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
        # both — :meth:`publish_chat` uses them to (a) enforce the
        # D.22 cross-channel guard and (b) persist the user message
        # to ``chat_messages`` at the same chokepoint as the chatJob
        # enqueue, so callers don't reach into the messages Book
        # directly. The cap is enforced by :meth:`MessageBook.add`
        # (the persistent layer is what compaction reads), not here.
        self._messages_book = messages_book
        self._conversations_book = conversations_book

    def _insert_pending(self, session, job: ChatJob, **_kwargs) -> _ChatJobRow:
        job_id = job.job_id or new_job_id()
        row = _ChatJobRow(
            job_id=job_id,
            conversation_id=job.conversation_id,
            correlation_id=job.correlation_id,
            text=job.text,
            channel=job.channel,
            contact_id=job.contact_id,
            caller_role=job.caller_role,
            chat_id=job.chat_id,
            tg_message_id=job.tg_message_id,
            kind=job.kind,
            task_id=job.task_id,
            manual=job.manual,
            received_seq=job.received_seq,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row

    def publish(self, job: ChatJob) -> str:
        """Enqueue one agent turn after the D.22 cross-channel guard.

        D.22 refuses the publish if the conversation exists and was
        created on a different channel — raises
        :class:`ChannelMismatchError` from the library Book it
        guards. The guard reads the typed ``contact_id`` /
        ``channel`` / ``conversation_id`` fields on the ChatJob so
        **any** caller of :meth:`publish` — :meth:`publish_chat`,
        internal steering republishes via
        :func:`submit_agent_message`, future dead-code producers —
        gets the same protection.

        Skipped when ``contact_id is None`` (task path with no
        contact), no ``conversations_book`` (legacy / tests), or
        no channel in the payload (a misconfigured job that the
        caller is responsible for).

        The per-turn text cap is **not** applied here — that lives
        in :meth:`MessageBook.add`, which is the chokepoint
        compaction reads. The chatJob payload carries the raw text;
        the LLM reads from ``chat_messages`` (post-cap).

        The activity stamp on ``contacts.last_seen_at`` is
        best-effort — a failure is logged and swallowed so a
        transient ``contact_book`` outage cannot block an inbound
        turn.
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
        message_id: str | None = None,
        # Channel-specific (typed, no `**extras`).
        chat_id: str | None = None,        # TG
        tg_message_id: int | None = None,  # TG
        kind: str | None = None,           # Task
        task_id: str | None = None,        # Task
        manual: bool | None = None,        # Task
    ) -> str:
        """Channel→agent convenience: build a ChatJob from channel args and enqueue.

        Single chokepoint for inbound turn intake. Delegates the
        enqueue + D.22 guard to :meth:`publish`; this method adds
        the user-message persistence step (which :meth:`publish`
        intentionally does not do — internal republishes via
        :func:`submit_agent_message` should not insert a new
        ``chat_messages`` row):

          1. :meth:`publish` runs the D.22 cross-channel guard and
             inserts the chatJob row. Raises
             :class:`ChannelMismatchError` before any write, so a
             mismatch doesn't leave an orphan row in
             ``chat_messages``.
          2. Persist the user message to ``chat_messages`` (the
             same row the agent's LLM call will read via
             :func:`build_messages_from_conversation`). The inbound
             cap (``system.chat_max_input_chars``) is enforced
             inside :meth:`MessageBook.add` — the chatJob payload
             carries the raw text and the persistent row carries
             the truncated text, so the LLM and compaction read
             the same shape.
          3. Stamp ``contacts.last_seen_at`` (best-effort).

        All channel-specific kwargs (``chat_id`` / ``tg_message_id``
        for TG; ``kind`` / ``task_id`` / ``manual`` for Task) are
        **typed** on the signature — there's no ``**extras`` bag,
        so a producer who misspells a field name gets an
        :class:`TypeError` at the call site instead of a silent
        null in the DB row.

        ``job_id`` and ``correlation_id`` are for callers that
        need stable idempotency keys (e.g. WebUI). When
        ``job_id`` is omitted the format is
        ``"{channel}:{uuid16}"``.

        ``message_id`` is the ULID to use for the
        ``chat_messages`` row. Defaults to a fresh UUID4 hex so
        concurrent retries produce distinct rows; pass the same
        value on retry for producer-side idempotency.

        ``contact_id`` is ``int | None`` because
        :class:`magi.bus.library.local.tasksBook.Task`-driven
        publishes can fire for a task with no bound contact; in
        that case the ChatJob is still enqueued but the D.22
        guard and ``last_seen_at`` stamp are skipped (no contact
        to validate / stamp).

        Returns the *job_id* of the published job.
        """
        resolved_job_id = job_id or f"{channel}:{uuid.uuid4().hex[:16]}"
        # Build the typed ChatJob — every field visible, no
        # black-box dict. The cap lives in :meth:`MessageBook.add`
        # (the layer compaction reads), so the chatJob carries
        # the raw text and the persistent row carries the
        # truncated text.
        job = ChatJob(
            job_id=str(resolved_job_id),
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            text=text,
            channel=channel,
            contact_id=contact_id,
            caller_role=caller_role,
            chat_id=chat_id,
            tg_message_id=tg_message_id,
            kind=kind,
            task_id=task_id,
            manual=manual,
        )
        # Enqueue first — :meth:`publish` runs the D.22 guard
        # and raises :class:`ChannelMismatchError` before any
        # write, so a mismatch doesn't leave an orphan row in
        # ``chat_messages``.
        jid = self.publish(job)
        # D.22 passed. Persist the user message to ``chat_messages``
        # so the agent's next ``build_messages_from_conversation``
        # read sees it. ``MessageBook.add`` enforces the inbound
        # cap on the row. Skipped when the board was constructed
        # without ``messages_book`` (legacy / tests). Best-effort:
        # the chatJob is already enqueued, so a failure here just
        # logs and moves on (the LLM still has the raw text via
        # the chatJob payload if it ever falls back to that).
        if self._messages_book is not None:
            try:
                self._messages_book.add(
                    conversation_id=conversation_id,
                    role="user",
                    text=text,
                    message_id=message_id,
                )
            except Exception:
                logger.exception(
                    "chatJobBoard.publish_chat: messages_book.add failed "
                    "(conversation=%s, channel=%s); chatJob %s enqueued without row",
                    conversation_id,
                    channel,
                    jid,
                )
        return jid

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


__all__ = [
    "ChatJob",
    "ChatJobResult",
    "chatJobBoard",
    "_ChatJobRow",
]
