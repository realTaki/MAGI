"""ConversationBook + MessageBook — chat conversation and message transcript.

Two tables:
- ``chat_conversations``  — one row per chat conversation (Crockford ULID primary key)
- ``chat_messages``  — one row per persisted transcript message

Plus a SQLite-only ``chat_messages_fts`` virtual table (FTS5,
``trigram`` tokeniser) with three triggers that keep it in lockstep
with ``chat_messages.id`` / ``chat_messages.text``.
``ensure_fts`` is idempotent — ``CREATE ... IF NOT EXISTS`` makes it
safe to run repeatedly.

Schema for ``chat_conversations`` + ``chat_messages`` tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base
from magi.bus.library.base import BaseBook

logger = logging.getLogger("magi.bus.library.local.conversationBook")

# -- inbound text cap ----------------------------------------------------
#
# Per-turn character cap applied at :meth:`MessageBook.add` to prevent a
# single runaway turn from blowing past the LLM context budget and
# breaking compaction's floor-of-1 guarantee (always keep the most
# recent turn). Lives in this module because the cap is fundamentally
# a *write-side* concern — the row is what compaction reads; the LLM
# reads the row, not a chatJob payload.
DEFAULT_CHAT_MAX_INPUT_CHARS = 8_000
MIN_CHAT_MAX_INPUT_CHARS = 1_000
MAX_CHAT_MAX_INPUT_CHARS = 100_000


def _clamp_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    """Parse + clamp a settings value. Garbage / missing → default."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _resolve_max_input_chars(settings_book) -> int:
    """Read ``system.chat_max_input_chars`` from a settings book, clamped.

    Safe to call with ``None`` (returns the default). The clamp
    range matches :mod:`magi.channels.api.system_settings` so the
    API surface and the runtime read can never diverge.
    """
    if settings_book is None:
        return DEFAULT_CHAT_MAX_INPUT_CHARS
    try:
        raw = settings_book.get(key="system.chat_max_input_chars")
    except Exception:
        return DEFAULT_CHAT_MAX_INPUT_CHARS
    return _clamp_int(
        raw,
        DEFAULT_CHAT_MAX_INPUT_CHARS,
        MIN_CHAT_MAX_INPUT_CHARS,
        MAX_CHAT_MAX_INPUT_CHARS,
    )


def _truncate_inbound(text: str, max_chars: int) -> tuple[str, bool, int]:
    """Truncate *text* to *max_chars* chars.

    Returns ``(text, was_truncated, original_len)``. A no-op when
    the text is already within budget — ``was_truncated`` is False
    in that case.
    """
    if len(text) <= max_chars:
        return text, False, len(text)
    return text[:max_chars], True, len(text)


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str  # 会话主键（Crockford ULID 字符串）
    delivery_address: str  # 渠道内的目标地址（如 tg chat_id）
    contact_id: int  # 会话所属联系人 ID
    channel: str  # 来源渠道（tg/webui/...）
    title: str | None = None  # 会话标题（auto-titled 或用户设置）
    summary: str | None = None  # cumulative compaction summary（auto-compact 生成）
    last_compaction_at: str | None = None  # 上次自动压缩的 ISO 时间
    created_at: str | None = None  # 创建时间
    updated_at: str | None = None  # 最近活动时间


@dataclass(frozen=True, slots=True)
class Message:
    id: int  # 主键（自增）
    conversation_id: str  # 所属会话 ID
    message_id: str  # 生产方幂等键（ULID）
    role: str  # 消息角色（user/assistant/system/tool）
    text: str  # 消息正文
    ts: str  # 消息时间戳（ISO 8601）
    archived: int = 0  # 1=被自动压缩归档
    content_blocks: list[dict[str, Any]] | None = None  # 富内容块（tool_use 等）
    llm_attempt_id: str | None = None  # 关联的 LLM 调用 ID（用于去重）


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One row of chat-history FTS5 search output.

    Carries the snippet (with literal ``<mark>...</mark>`` tags
    already inserted by ``snippet(chat_messages_fts, ...)``) and
    the bm25 score (lower = better). ``conversation_id`` / ``message_id``
    let the caller resolve the hit back to its
    :class:`Message` / :class:`Conversation` row.
    """

    conversation_id: str  # 命中消息所属会话 ID
    message_id: str  # 命中消息的 message_id
    role: str  # 命中消息的角色
    ts: str  # 命中消息的时间戳
    snippet: str  # 含 <mark> 高亮的片段
    score: float  # bm25 评分（越低越相关）
    channel: str  # 命中消息的渠道
    title: str | None = None  # 所属会话的标题
    delivery_address: str | None = None  # 所属会话的目标地址


class SearchUnavailable(RuntimeError):
    """SQLite in this deployment was built without the FTS table.

    The FTS5 virtual table is built by
    :func:`install_conversation_fts_schema`. When it has not been
    run — typically because the
    SQLite build lacks FTS5, or because the bootstrap ran before the
    FTS installer — ``MessageBook.search`` raises this instead of
    returning empty results, so callers can surface a 503 rather than
    a silently-empty search box.
    """


class ConversationPathError(ValueError):
    """The ``conversation_id`` string is structurally invalid.

    Raised when the caller passes an
    id that doesn't decode as a Crockford ULID (the schema's
    primary key format). Distinct from "conversation not found":
    a malformed id is a 400, not a 404, and the caller
    shouldn't retry with the same value.
    """


class ConversationCorruptError(RuntimeError):
    """The conversation row on disk is malformed.

    Raised when JSON decoding fails, a required column is
    missing, or the persisted shape doesn't match the
    current ``Conversation`` schema. Surface as 500 — the data
    is unrecoverable without operator intervention.
    """


class ConversationNotFoundError(LookupError):
    """The conversation id is well-formed but doesn't belong to
    this operator (or was deleted between the list call
    and the lookup).

    Distinct from :class:`ConversationPathError` (which is "the
    id is malformed"): here the id is valid, the operator
    is just not allowed to see it / it doesn't exist.
    Surface as 404.
    """


class ChannelMismatchError(ValueError):
    """The conversation was created on a different channel than
    the one the caller is writing from (D.22 cross-channel
    guard). Carries ``conversation_channel`` so the caller can
    surface which channel owns the conversation.

    Example: a WebUI ``POST /chat/send`` targeting a
    conversation originally created on TG → 403 with a hint
    to continue the conversation on the original channel.
    """

    def __init__(self, conversation_channel: str) -> None:
        super().__init__(
            f"Conversation owned by channel {conversation_channel!r}; "
            "cross-channel writes are not allowed."
        )
        self.conversation_channel = conversation_channel


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """Input shape for :meth:`ConversationBook.append_messages`.

    Carries the bare minimum needed to persist one inbound
    message row — role, text, timestamp, and a stable
    message_id for producer-side idempotency.
    """

    role: str  # 消息角色（user/assistant/system/tool）
    text: str  # 消息正文
    ts: str  # 消息时间戳（ISO 8601）
    message_id: str  # 生产方幂等键


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """Lightweight projection of :class:`Conversation` for the
    list-endpoint (``GET /api/chat/conversations``).

    Carries enough to render the sidebar row (id, title /
    preview, timestamps, channel, message count) without
    pulling the full transcript. The list endpoint fans
    these out in bulk; the full :class:`Conversation` (with
    ``messages``) is fetched only when the operator opens
    a row.
    """

    conversation_id: str  # 会话 ID
    channel: str  # 渠道
    created_at: str  # 创建时间
    updated_at: str  # 最近活动时间
    message_count: int  # 消息总数（active）
    preview: str  # 最新一条消息预览
    title: str | None = None  # 会话标题


@dataclass(frozen=True, slots=True)
class ResolvedHit:
    """A search hit after cross-contact validation + context fetch.

    Returned by :meth:`MessageBook.resolve_hit` — the single helper
    that closes the gap between the FTS row (which only carries a
    ``message_id``, a snippet, and a bm25 score) and the full picture
    the renderer / API consumer needs.

    ``conversation`` is the owning conversation header. Always already
    contact_id-checked — if the hit pointed at another operator's conversation,
    ``resolve_hit`` returns ``None`` instead of a partial envelope.

    ``is_archived`` is True when the hit landed on a row that
    auto-compaction rolled out (``chat_messages.archived == 1``).
    Archived hits carry no clean neighbour, so ``messages_with_hit``
    is empty and ``hit_position`` is ``-1`` — the caller emits the
    snippet only (the LLM tool renders this as ``(archived) snippet:
    ...``; the future ``/api/chat/search`` HTTP endpoint will mirror
    the same shape).

    ``messages_with_hit`` is the **active** subset of the conversation
    messages, sliced ±``context_n`` around the hit. Length is
    ``2 * context_n + 1`` in the middle of a long conversation, shorter
    near conversation boundaries, and zero when ``is_archived`` or
    ``context_n == 0``.

    ``hit_position`` is the index of the hit inside
    ``messages_with_hit``. The renderer re-attaches the snippet's
    ``<mark>`` highlighting at this position (so the LLM sees where
    in the message the FTS match landed, not just the surrounding
    text).
    """

    conversation: Conversation  # 命中所属的会话头
    hit: SearchHit  # 原始 FTS 命中行
    is_archived: bool  # True=命中已归档消息
    messages_with_hit: list[Message]  # 命中周围的活动消息切片
    hit_position: int  # 命中所处位置（-1 表示无上下文）


# -- internal ORM --------------------------------------------------------


class _ConversationRow(Base):
    __tablename__ = "chat_conversations"

    conversation_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    delivery_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)


class _MessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("chat_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_chat_messages_conversation_archived", "conversation_id", "archived", "id"),
        UniqueConstraint("conversation_id", "message_id", name="uq_chat_messages_conv_msg"),
    )


# -- Books ---------------------------------------------------------------


class ConversationBook(BaseBook[_ConversationRow, Conversation]):
    model_cls = _ConversationRow
    dto_cls = Conversation

    def __init__(self, factory, *, settings_book=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(factory)
        # ``settings_book`` is optional so unit tests can build a
        # ConversationBook with just a factory. When present, the
        # cap is forwarded to any internal :class:`MessageBook` this
        # book constructs (in :meth:`append_messages` and
        # :meth:`get_messages_page`), so the persistent layer
        # enforces the same cap that
        # :meth:`chatJobBoard.publish_chat` enforces on the LLM input.
        self._settings_book = settings_book

    def get(self, *, conversation_id: str) -> Conversation | None:
        with self._session() as s:
            row = s.scalar(
                select(_ConversationRow).where(_ConversationRow.conversation_id == conversation_id)
            )
            return self._row_to_dto(row) if row else None

    def resolve_delivery_address(self, *, conversation_id: str) -> str | None:
        """Return the ``delivery_address`` for a conversation, or ``None``."""
        conv = self.get(conversation_id=conversation_id)
        return conv.delivery_address if conv is not None else None

    def get_for_owner(self, *, contact_id: int, conversation_id: str) -> Conversation | None:
        """``get`` with cross-contact defence-in-depth.

        The previous ``SessionService.get`` accepted ``(contact_id,
        conversation_id)`` and silently dropped rows that didn't match
        the caller's contact_id; :meth:`get` accepts
        ``conversation_id`` only, which would let a caller guess another
        operator's ``conversation_id`` and pull its header back. The
        FTS5 search path is already scoped by ``WHERE s.contact_id = :contact_id``
        inside the JOIN, so a tool that only goes through
        :meth:`MessageBook.search` is safe — but the moment any
        caller resolves a hit back through ``conversations_book.get``
        (e.g. to render a context slice, or for the future
        ``/api/chat/search`` HTTP endpoint), they need the contact_id
        check to live somewhere.

        This method is the single home for that check: returns the
        conversation **only** if ``contact_id`` owns it, otherwise ``None``.
        Both the LLM tool and the HTTP API route through here, so
        the cross-contact defence lives in one place rather than
        being re-implemented (and forgotten) at every call site.
        """
        conversation = self.get(conversation_id=conversation_id)
        if conversation is None:
            return None
        if conversation.contact_id != contact_id:
            return None
        return conversation

    def list_for_owner(self, *, contact_id: int) -> list[Conversation]:
        with self._session() as s:
            rows = s.scalars(
                select(_ConversationRow)
                .where(_ConversationRow.contact_id == contact_id)
                .order_by(_ConversationRow.updated_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(
        self,
        *,
        delivery_address: str,
        contact_id: int,
        channel: str,
        title: str | None = None,
    ) -> Conversation:
        """Create a new conversation row.

        ``conversation_id``, ``created_at`` and ``updated_at`` are owned
        by this Book — callers never pass them. ``conversation_id``
        comes back in the returned :class:`Conversation` for callers to
        thread into follow-up calls (``append_messages``, ``touch``,
        ``set_summary``, …).
        """
        import uuid
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        conversation_id = uuid.uuid4().hex[:16]
        with self._session() as s:
            row = _ConversationRow(
                conversation_id=conversation_id,
                delivery_address=delivery_address,
                contact_id=contact_id,
                channel=channel,
                title=title,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def get_or_create_for_tg(
        self,
        *,
        contact_id: int,
        delivery_address: str,
    ) -> Conversation:
        """Get the TG conversation owned by *contact_id* at *delivery_address*, or create one.

        Idempotent — if a conversation already exists for this
        ``(contact_id, delivery_address)`` pair it is returned as-is;
        otherwise a new conversation is created with ``channel="tg"``.

        The match key is ``delivery_address`` (the TG chat_id) rather
        than ``contact_id``. TG is DM-only today so the two are 1:1 and
        either key resolves — but anchoring on ``delivery_address`` keeps
        the per-conversation identity correct if group chats are ever added.
        """
        conversations = self.list_for_owner(contact_id=contact_id)
        for c in conversations:
            if c.channel == "tg" and c.delivery_address == delivery_address:
                return c
        return self.add(
            delivery_address=delivery_address,
            contact_id=contact_id,
            channel="tg",
        )

    def create_task_conversation(
        self,
        *,
        contact_id: int,
        title: str,
        delivery_address: str = "",
        channel: str = "webui",
    ) -> str:
        """Create a new conversation for a scheduled task.

        Returns the new ``conversation_id`` so the caller can stamp it
        onto the Task row.
        """
        conv = self.add(
            delivery_address=delivery_address,
            contact_id=contact_id,
            channel=channel,
            title=title,
        )
        return conv.conversation_id

    def append_messages(
        self,
        contact_id: int,
        conversation_id: str,
        messages: list[ConversationMessage],
        *,
        channel: str,
    ) -> list[Message]:
        """Atomically append one or more messages to a conversation.

        D.22 cross-channel guard: if the conversation row's ``channel``
        column doesn't match *channel*, raises
        :class:`ChannelMismatchError`. The caller should surface a
        403 — two LLM loops from different channels MUST NOT write
        into the same history.

        Each :class:`ConversationMessage` is written to ``chat_messages``
        via :class:`MessageBook` in a single transaction so the
        append group is all-or-nothing. Returns the list of
        persisted :class:`Message` rows in insertion order.
        """
        from datetime import datetime

        # D.22: verify conversation ownership and channel match.
        conversation = self.get_for_owner(contact_id=contact_id, conversation_id=conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(
                f"Conversation {conversation_id} not found for contact_id {contact_id}"
            )
        if conversation.channel != channel:
            raise ChannelMismatchError(conversation.channel)

        now = datetime.now(UTC).isoformat()
        self.touch(conversation_id=conversation_id, updated_at=now)

        message_book = MessageBook(self._factory)
        persisted: list[Message] = []
        for sm in messages:
            msg = message_book.add(
                conversation_id=conversation_id,
                role=sm.role,
                text=sm.text,
                message_id=sm.message_id,
                ts=sm.ts,
            )
            persisted.append(msg)
        return persisted

    def get_messages_page(
        self,
        contact_id: int,
        conversation_id: str,
        *,
        limit: int,
        offset: int,
        include_archived: bool = False,
    ) -> tuple[list[Message], int, int]:
        """Paginated message slice, scoped to ``contact_id``'s conversation.

        Thin facade over :meth:`MessageBook.list_for_conversation_page`
        that closes the seam between the two Books: callers holding a
        ``ConversationBook`` (which is the natural injection point —
        it owns the conversation identity and the ownership check)
        don't have to also inject a ``MessageBook`` for the paginated
        read path.

        Ownership validation is delegated to
        :meth:`get_for_owner`: if the conversation doesn't belong to
        ``contact_id``, raises the same ``ConversationNotFoundError``
        the rest of the Book surface raises, so callers don't need a
        special branch for "wrong owner".
        """
        if self.get_for_owner(contact_id=contact_id, conversation_id=conversation_id) is None:
            raise ConversationNotFoundError(conversation_id)
        message_book = MessageBook(self._factory)
        return message_book.list_for_conversation_page(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )

    def touch(self, *, conversation_id: str, updated_at: str) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_ConversationRow).where(_ConversationRow.conversation_id == conversation_id)
            )
            if row is None:
                return
            row.updated_at = updated_at
            s.commit()

    def set_title_if_null(
        self,
        *,
        contact_id: int,
        conversation_id: str,
        title: str,
        bump_updated: bool = True,
    ) -> Conversation | None:
        """[claude, 2026-08-08] CAS-style title set — only writes if currently NULL.

        Required by :func:`magi.agent.auto_title.request_conversation_title`.
        Scope by ``(contact_id, conversation_id)`` (cross-contact defence), only set
        when existing ``title`` is NULL, optionally bump ``updated_at``.

        Returns the updated :class:`Conversation` on success (lost the race
        to another writer that already set a title), or ``None`` when
        no matching row was found.
        """
        from magi.bus.db.base import utcnow_naive

        now = utcnow_naive().isoformat() + "Z"
        with self._session() as s:
            stmt = (
                update(_ConversationRow)
                .where(
                    _ConversationRow.conversation_id == conversation_id,
                    _ConversationRow.contact_id == contact_id,
                    _ConversationRow.title.is_(None),
                )
                .values(title=title)
            )
            if bump_updated:
                stmt = stmt.values(updated_at=now)
            result = s.execute(stmt)
            if getattr(result, "rowcount", 0) == 0:  # type: ignore[reportAttributeAccessIssue]
                s.rollback()
                return None
            s.commit()
            row = s.scalar(
                select(_ConversationRow).where(_ConversationRow.conversation_id == conversation_id)
            )
            return self._row_to_dto(row) if row else None

    def set_summary(
        self,
        *,
        contact_id: int,
        conversation_id: str,
        summary: str,
        bump_updated: bool = True,
    ) -> Conversation | None:
        """[claude, 2026-08-12] Overwrite Conversation.summary, stamp last_compaction_at.

        Write-back primitive for auto-compaction. Not CAS — incremental
        compaction always wins; a concurrent pass only produces a
        fresher tail and the later write supersedes. CAS would silently
        drop the second writer, which is wrong for an incremental
        pipeline. Scope by ``(contact_id, conversation_id)`` for
        cross-contact defence. Returns the updated DTO, or ``None`` if
        the conversation no longer exists.
        """
        from magi.bus.db.base import utcnow_naive

        now = utcnow_naive().isoformat() + "Z"
        with self._session() as s:
            stmt = (
                update(_ConversationRow)
                .where(
                    _ConversationRow.conversation_id == conversation_id,
                    _ConversationRow.contact_id == contact_id,
                )
                .values(summary=summary, last_compaction_at=now)
            )
            if bump_updated:
                stmt = stmt.values(updated_at=now)
            result = s.execute(stmt)
            if getattr(result, "rowcount", 0) == 0:  # type: ignore[reportAttributeAccessIssue]
                s.rollback()
                return None
            s.commit()
            row = s.scalar(
                select(_ConversationRow).where(
                    _ConversationRow.conversation_id == conversation_id
                )
            )
            return self._row_to_dto(row) if row else None


class MessageBook(BaseBook[_MessageRow, Message]):
    model_cls = _MessageRow
    dto_cls = Message

    def __init__(self, factory, *, settings_book=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(factory)
        # ``settings_book`` is optional so unit tests can build a
        # MessageBook with just a factory and skip the
        # ``system.chat_max_input_chars`` cap. When present, the
        # cap is applied on :meth:`add` so a single huge turn
        # cannot break compaction's floor-of-1 guarantee.
        self._settings_book = settings_book

    def get(self, *, message_id: int) -> Message | None:
        with self._session() as s:
            row = s.scalar(select(_MessageRow).where(_MessageRow.id == message_id))
            return self._row_to_dto(row) if row else None

    def list_for_conversation(
        self, *, conversation_id: str, include_archived: bool = False
    ) -> list[Message]:
        with self._session() as s:
            stmt = select(_MessageRow).where(_MessageRow.conversation_id == conversation_id)
            if not include_archived:
                stmt = stmt.where(_MessageRow.archived == 0)
            stmt = stmt.order_by(_MessageRow.id)
            rows = s.scalars(stmt).all()
            return [self._row_to_dto(r) for r in rows]

    def list_for_conversation_page(
        self,
        *,
        conversation_id: str,
        limit: int,
        offset: int,
        include_archived: bool = False,
    ) -> tuple[list[Message], int, int]:
        """Paginated slice of ``conversation_id``'s messages.

        Returns ``(messages, total_active, total_all)``:
          - ``messages``: the requested page, oldest-first within the page
            (so the WebUI can render top-down). Ordering is by
            ``chat_messages.id ASC`` — i.e. insertion order, which is
            identical to chronological order for messages inserted in a
            single transaction.
          - ``total_active``: count of non-archived rows for the page
            bookkeeping (the UI uses ``loaded_count < total_active`` to
            decide whether to show the "load older messages" affordance).
          - ``total_all``: count including archived rows, so the UI can
            surface a "(+N archived)" hint if there's history the user
            hasn't seen since compaction rolled it out.

        ``limit`` / ``offset`` are clamped by the caller — the route
        handler in :mod:`magi.channels.api.chat_conversations` does it inline
        because ``Query(ge=…, le=…)`` interacts badly with
        ``from __future__ import annotations`` for some pydantic
        versions.
        """
        from sqlalchemy import func

        with self._session() as s:
            base = select(_MessageRow).where(_MessageRow.conversation_id == conversation_id)
            archived_filter = [] if include_archived else [_MessageRow.archived == 0]

            page_rows = s.scalars(
                base.where(*archived_filter).order_by(_MessageRow.id).limit(limit).offset(offset)
            ).all()
            total_active = (
                s.scalar(
                    select(func.count())
                    .select_from(_MessageRow)
                    .where(_MessageRow.conversation_id == conversation_id)
                    .where(_MessageRow.archived == 0)
                )
                or 0
            )
            total_all = (
                s.scalar(
                    select(func.count())
                    .select_from(_MessageRow)
                    .where(_MessageRow.conversation_id == conversation_id)
                )
                or 0
            )
        return (
            [self._row_to_dto(r) for r in page_rows],
            int(total_active),
            int(total_all),
        )

    def add(
        self,
        *,
        conversation_id: str,
        role: str,
        text: str,
        message_id: str | None = None,
        ts: str | None = None,
        content_blocks: list[dict[str, Any]] | None = None,
        llm_attempt_id: str | None = None,
    ) -> Message:
        """Add one message row."""
        import uuid
        from datetime import datetime

        # Per-turn cap: protect the persistent layer (and therefore
        # compaction, which reads from here) from a single runaway
        # turn. This is the single chokepoint for the cap — the
        # chatJob does not need a parallel cap, since the LLM
        # reads the truncated row via
        # :func:`build_messages_from_conversation`, not a chatJob
        # payload.
        max_chars = _resolve_max_input_chars(self._settings_book)
        text, was_truncated, original_len = _truncate_inbound(text, max_chars)
        if was_truncated:
            logger.warning(
                "MessageBook.add: truncated %d→%d chars (cap=%d, role=%s)",
                original_len,
                max_chars,
                max_chars,
                role,
            )

        if message_id is None:
            message_id = uuid.uuid4().hex
        if ts is None:
            ts = datetime.now(UTC).isoformat()
        with self._session() as s:
            row = _MessageRow(
                conversation_id=conversation_id,
                message_id=message_id,
                role=role,
                text=text,
                ts=ts,
                content_blocks=content_blocks,
                llm_attempt_id=llm_attempt_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def archive(self, *, message_id: int) -> None:
        with self._session() as s:
            row = s.scalar(select(_MessageRow).where(_MessageRow.id == message_id))
            if row is None:
                return
            row.archived = 1
            s.commit()

    # -- full-text search ------------------------------------------------

    def ensure_fts(self) -> None:
        """Install the FTS5 virtual table + sync triggers if missing.

        Idempotent: every statement uses ``IF NOT EXISTS``. Safe to
        call from bootstrap on every process start; the second-and-
        later invocations are no-ops.

        Only does anything on a SQLite engine — the FTS5 module is
        SQLite-specific, so on the MAGIS PostgreSQL factory this is
        a no-op (PG would need a different index strategy; out of
        scope for this migration).
        """
        install_conversation_fts_schema(self._factory.engine)

    def search(
        self,
        *,
        contact_id: int,
        q: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SearchHit], int]:
        """Full-text search across ``chat_messages`` rows owned by ``contact_id``.

        Scoping: ``WHERE s.contact_id = :contact_id`` is part of the join, not a
        post-filter — so the bm25 ranking is computed on the
        contact's own corpus, never on someone else's. ``q`` is
        whitespace-tokenised into quoted ``"<token>"`` substrings,
        mirroring the sanitiser. The MATCH expression uses
        the trigram tokeniser built into the FTS5 schema (3+ char
        CJK runs work without explicit segmentation).

        Returns ``(hits, total)``; ``total`` is the total matching
        rows across the caller's corpus, not the page size, so the
        caller can render "N match(es) total".

        Raises :class:`SearchUnavailable` if the FTS table is
        absent (SQLite built without FTS5, or ``ensure_fts`` not yet
        run). Lets the caller surface a 503 rather than a silent
        empty box.
        """
        if not q or not q.strip():
            return [], 0
        match = " ".join(
            f'"{token.replace(chr(34), "").strip()}"'
            for token in q.split()
            if token.replace(chr(34), "").strip()
        )
        if not match:
            return [], 0

        base = (
            "FROM chat_messages_fts "
            "JOIN chat_messages m ON m.id = chat_messages_fts.rowid "
            "JOIN chat_conversations c ON c.conversation_id = m.conversation_id "
            "WHERE chat_messages_fts MATCH :match AND c.contact_id = :contact_id"
        )
        with self._session() as s:
            available = s.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages_fts'")
            ).first()
            if available is None:
                raise SearchUnavailable("Full-text search is not available in this SQLite build")
            total = s.execute(
                text("SELECT COUNT(*) " + base),
                {"match": match, "contact_id": contact_id},
            ).scalar_one()
            rows = s.execute(
                text(
                    "SELECT m.conversation_id, m.message_id, m.role, m.ts, "
                    "c.title, c.channel, c.delivery_address, "
                    "snippet(chat_messages_fts, 0, '<mark>', '</mark>', "
                    "'…', 16) AS snippet, "
                    "bm25(chat_messages_fts) AS score "
                    + base
                    + " ORDER BY score LIMIT :limit OFFSET :offset"
                ),
                {"match": match, "contact_id": contact_id, "limit": limit, "offset": offset},
            ).fetchall()

        return [
            SearchHit(
                conversation_id=row.conversation_id,
                message_id=row.message_id,
                role=row.role,
                ts=row.ts,
                snippet=row.snippet,
                score=float(row.score),
                channel=row.channel,
                title=row.title,
                delivery_address=row.delivery_address,
            )
            for row in rows
        ], total

    # -- hit resolution ---------------------------------------------------

    def resolve_hit(
        self,
        *,
        contact_id: int,
        hit: SearchHit,
        context_n: int,
        conversations_book: ConversationBook,
    ) -> ResolvedHit | None:
        """Resolve a search hit to its full context.

        This is the shared **business logic** every consumer of
        search needs in common — the LLM tool and the future
        ``/api/chat/search`` HTTP endpoint both need to:

          1. Validate the hit's conversation belongs to ``contact_id`` (the
             FTS query is already scoped by ``c.contact_id = :contact_id`` in
             the JOIN, but a render-time defence-in-depth check
             keeps the gap closed if a future caller ever
             short-circuits the FTS layer).
          2. Fetch the hit's surrounding messages (±``context_n``).
          3. Distinguish archived hits (snippet-only, no
             neighbour) from active hits (sliced context slice).

        Centralising this in one Book method means the per-contact
        safety check, the active-vs-archive classification, and
        the context-window slicing all live in a single place
        rather than being re-implemented (and possibly
        forgotten) at every call site.

        ``conversations_book`` is passed explicitly rather than held
        on ``self`` because ``MessageBook`` doesn't otherwise
        need a reference to its sibling — keeping the Book's
        dependency surface minimal. Bootstrap wires both Books
        off the same factory and the caller always has both
        handy (via ``bus.messages_book`` / ``bus.conversations_book``).

        Returns ``None`` when:
          - the hit's conversation doesn't belong to ``contact_id`` (cross-
            contact leak attempt; ``get_for_owner`` returned None)
          - the hit row was deleted between FTS read and now
            (race)

        In both cases the caller emits a generic
        "conversation no longer accessible" hint instead of leaking
        the row's metadata.

        For archived hits or ``context_n == 0``, returns a
        ``ResolvedHit`` with ``is_archived=True`` (or
        ``messages_with_hit=[]``) and ``hit_position=-1``.
        """
        if context_n < 0:
            context_n = 0

        conversation = conversations_book.get_for_owner(
            contact_id=contact_id,
            conversation_id=hit.conversation_id,
        )
        if conversation is None:
            return None

        # One fetch covers both branches: active hits (the common
        # case) and archived hits (rare — auto-compaction only
        # flips the flag, never reorders rows). The combined list
        # is sorted by row id which is monotonic per conversation.
        messages = self.list_for_conversation(
            conversation_id=hit.conversation_id,
            include_archived=True,
        )

        # Find the hit's combined-list index.
        hit_idx: int | None = None
        for i, m in enumerate(messages):
            if m.message_id == hit.message_id:
                hit_idx = i
                break
        if hit_idx is None:
            return None

        is_archived = messages[hit_idx].archived == 1
        if is_archived or context_n == 0:
            # Archived: no clean neighbour. ``context_n == 0``:
            # caller asked for snippet-only by choice. Both branches
            # render the same way (tool: ``(archived) snippet``;
            # API: ``{ archived: true, snippet: ... }``).
            return ResolvedHit(
                conversation=conversation,
                hit=hit,
                is_archived=True,
                messages_with_hit=[],
                hit_position=-1,
            )

        # Active: slice the **active subset** around the hit.
        # Archived rows were rolled out by auto-compaction and
        # don't form a coherent "around the hit" neighbourhood.
        active_msgs = [m for m in messages if m.archived == 0]
        active_idx: int | None = None
        for i, m in enumerate(active_msgs):
            if m.message_id == hit.message_id:
                active_idx = i
                break
        if active_idx is None:
            # Hit row's ``archived`` flag flipped between the
            # combined read and now (race with compaction).
            # Treat as archived for safety.
            return ResolvedHit(
                conversation=conversation,
                hit=hit,
                is_archived=True,
                messages_with_hit=[],
                hit_position=-1,
            )

        lo = max(0, active_idx - context_n)
        hi = min(len(active_msgs), active_idx + context_n + 1)
        return ResolvedHit(
            conversation=conversation,
            hit=hit,
            is_archived=False,
            messages_with_hit=active_msgs[lo:hi],
            hit_position=active_idx - lo,
        )


# -- FTS5 schema installer ----------------------------------------------


_FTS5_DDL = (
    # The FTS5 virtual table mirrors chat_messages.text as an
    # external-content index; rowid pinned to chat_messages.id so
    # the triggers below can address rows by id.
    "CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5("
    "text, content='chat_messages', content_rowid='id', "
    "tokenize='trigram')",
    # Sync triggers — kept in sync by ``synchronise_schema`` at boot.
    "CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages BEGIN "
    "INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN "
    "INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) "
    "VALUES ('delete', old.id, old.text); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN "
    "INSERT INTO chat_messages_fts(chat_messages_fts, rowid, text) "
    "VALUES ('delete', old.id, old.text); "
    "INSERT INTO chat_messages_fts(rowid, text) VALUES (new.id, new.text); "
    "END",
)


def install_conversation_fts_schema(engine) -> None:
    """Install the FTS5 schema on a SQLite engine.

    No-op on non-SQLite engines (PG would need a different index
    strategy). Safe to call repeatedly — every statement uses
    ``IF NOT EXISTS``. The bootstrap calls this once after wiring
    the local factory; tests that build a fresh SQLite file call it
    after ``create_all``.
    """
    if not engine.dialect.name.startswith("sqlite"):
        return
    with engine.begin() as conn:
        for stmt in _FTS5_DDL:
            conn.exec_driver_sql(stmt)


__all__ = [
    "Conversation",
    "Message",
    "ConversationMessage",
    "SearchHit",
    "SearchUnavailable",
    "ResolvedHit",
    "ConversationBook",
    "MessageBook",
    "ChannelMismatchError",
    "ConversationPathError",
    "ConversationCorruptError",
    "ConversationNotFoundError",
    "install_conversation_fts_schema",
    "_ConversationRow",
    "_MessageRow",
]
