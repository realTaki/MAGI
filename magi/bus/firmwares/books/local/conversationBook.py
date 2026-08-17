"""ConversationBook + MessageBook — chat conversation and message transcript.

Two tables:
- ``chat_conversations``  — one row per chat conversation (``BaseRecordMixin.id`` 自增主键即会话身份)
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
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    select,
    text,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from magi.bus.bases.db.base import enum_column, utcnow_naive

logger = logging.getLogger("magi.bus.firmwares.books.local.conversationBook")


class AgentMessageRole(StrEnum):
    """Closed set of roles stored on ``Message.role``.

    Values mirror the on-the-wire LLM message-protocol role
    names (``"user"`` / ``"assistant"`` / ``"system"`` /
    ``"tool"``) so the DB row's ``role`` column drops straight
    into the dict the providers (Anthropic / OpenAI) accept
    without a translation hop. Adding a new role (e.g.
    ``"developer"`` for OpenAI's newer spec) requires a schema
    migration.

    ``StrEnum`` rather than bare constants so typos are caught
    at lookup time instead of silently comparing False: every
    member is still a ``str`` (``AgentMessageRole.USER == "user"``),
    so ``m.role in ("user", "system")`` checks, Pydantic
    ``role: str`` fields, ``json.dumps`` serialisation, and the
    ``role.upper()`` call in :mod:`magi.agent.compaction` keep
    working unchanged. Mirrors
    :class:`magi.bus.firmwares.books.local.contactBook.NoteKind`.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# -- public dataclasses --------------------------------------------------


#: Semantic alias for a channel name stored on :attr:`Conversation.channel`.
#:
#: The channel set is **dynamic** — workers advertise their channel via
#: :meth:`SettingBook.register_channel` at startup, and :meth:`SettingBook.channel_options`
#: returns the live list. The DTO therefore keeps ``channel`` as ``str``
#: (the column stays ``Mapped[str]`` + ``Text`` — no schema migration) and
#: relies on :meth:`ConversationBook._validate_add` to reject unregistered
#: values at the write boundary.
#:
#: This is a plain alias (not ``NewType``) so callers can keep passing raw
#: strings at every existing call site without type-checker friction.
#: The runtime contract — :class:`ChannelNotRegisteredError` raised at the
#: write boundary — is the actual safety net; this alias exists only so
#: docstrings and future function signatures can refer to "ChannelName"
#: without inventing a one-letter class.
ChannelName = str


class ChannelNotRegisteredError(ValueError):
    """``Conversation.channel`` is not a currently-registered channel.

    Raised by :meth:`ConversationBook.add` / :meth:`update` when the supplied
    channel string isn't in :meth:`SettingBook.channel_options`. The
    registered channel set is dynamic — workers register at startup, the
    Book just enforces the contract at the write boundary.
    """

    def __init__(self, channel: str, registered: list[str]) -> None:
        super().__init__(
            f"channel {channel!r} is not registered in settings_book; "
            f"registered: {registered!r}"
        )
        self.channel = channel
        self.registered = registered


@dataclass(frozen=True, slots=True, kw_only=True)
class Conversation(BaseRecord):
    delivery_address: str
    contact_id: int
    channel: ChannelName  # must be in settings_book.channel_options(); validated at write
    title: str | None = None
    summary: str | None = None
    last_compaction_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Message(BaseRecord):
    conversation_id: int  # 所属会话的内部自增 ID（= chat_conversations.id）
    role: AgentMessageRole
    text: str
    ts: datetime = field(default_factory=utcnow_naive)
    archived: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchHit:
    """One row of chat-history FTS5 search output.

    Carries the snippet (with literal ``<mark>...</mark>`` tags
    already inserted by ``snippet(chat_messages_fts, ...)``) and
    the bm25 score (lower = better). ``conversation_id`` / ``message_id``
    let the caller resolve the hit back to its
    :class:`Message` / :class:`Conversation` row.
    """

    conversation_id: int  # 命中消息所属会话 ID（chat_conversations.id）
    message_id: int  # 命中消息的 message id（chat_messages.id）
    role: str  # 命中消息的角色
    ts: datetime  # 命中消息的时间戳
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationMessage:
    """Input shape for :meth:`ConversationBook.append_messages`.

    Carries the bare minimum needed to persist one inbound
    message row — role, text, timestamp, and a stable
    message_id for producer-side idempotency.
    """

    role: AgentMessageRole  # 消息角色（user/assistant/system/tool）
    text: str  # 消息正文
    ts: datetime  # 消息时间戳（naive UTC）


@dataclass(frozen=True, slots=True, kw_only=True)
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


class _ConversationRow(BaseRecordMixin):
    __tablename__ = "chat_conversations"

    # 会话身份 = 基类自增 ``id``（物理外键与业务引用都指向它）。
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_compaction_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class _MessageRow(BaseRecordMixin):
    __tablename__ = "chat_messages"

    conversation_row_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation: Mapped[_ConversationRow] = relationship()
    role: Mapped[AgentMessageRole] = mapped_column(
        enum_column(AgentMessageRole), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_chat_messages_conversation_archived", "conversation_row_id", "archived", "id"),
    )


# -- Books ---------------------------------------------------------------


class ConversationBook(BaseBook[_ConversationRow, Conversation]):
    model_cls = _ConversationRow
    record_cls = Conversation

    def __init__(self, factory, *, settings_book=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(factory)
        # ``settings_book`` is required for :meth:`_validate_add`'s channel
        # check. In tests it's passed by the ``factory`` fixture; in
        # production it's wired by :func:`magi.bus.bootstrap.build_local_bus`
        # before any conversation is created.
        self._settings_book = settings_book

    def _validate_add(self, record: Conversation) -> None:
        """Reject ``Conversation.channel`` values not in the live registry.

        The channel set is dynamic — workers register at startup
        (``SettingBook.register_channel``) — so we don't enum-ify the
        column at the schema level. Instead this single chokepoint
        enforces the contract: any ``add`` / ``update`` that carries an
        unregistered channel fails fast with :class:`ChannelNotRegisteredError`,
        before SQLAlchemy sees it. A missing ``settings_book`` (older
        bootstrap paths, narrow test setups) silently skips the check —
        the row's column type is still ``str``, so no value is rejected.
        """
        if self._settings_book is None:
            return
        registered = self._settings_book.channel_options()
        if record.channel not in registered:
            raise ChannelNotRegisteredError(record.channel, registered)

    def resolve_delivery_address(self, *, conversation_id: int) -> str | None:
        """Return the ``delivery_address`` for a conversation, or ``None``."""
        conv = self.get(conversation_id)
        return conv.delivery_address if conv is not None else None

    def get_for_owner(self, *, contact_id: int, conversation_id: int) -> Conversation | None:
        """``get`` with cross-contact defence-in-depth.

        :meth:`get` accepts ``conversation_id`` (the internal id) only,
        which would let a caller guess another operator's conversation
        and pull its header back. The FTS5 search path is already
        scoped by ``WHERE c.contact_id = :contact_id`` inside the
        JOIN, so a tool that only goes through :meth:`MessageBook.search`
        is safe — but the moment any caller resolves a hit back
        through ``conversations_book.get`` (e.g. to render a context
        slice, or for the future ``/api/chat/search`` HTTP endpoint),
        they need the contact_id check to live somewhere.

        This method is the single home for that check: returns the
        conversation **only** if ``contact_id`` owns it, otherwise ``None``.
        Both the LLM tool and the HTTP API route through here, so
        the cross-contact defence lives in one place rather than
        being re-implemented (and forgotten) at every call site.
        """
        conversation = self.get(conversation_id)
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

    def list_page_for_owner(
        self,
        *,
        contact_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        """Return one newest-first page of this owner's conversations.

        This is deliberately a plain :class:`Conversation` query.  The
        conversation-list API currently exposes no message-derived preview
        or count, so it does not need a special aggregate DTO.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        with self._session() as s:
            total = int(
                s.scalar(
                    select(func.count())
                    .select_from(_ConversationRow)
                    .where(_ConversationRow.contact_id == contact_id)
                )
                or 0
            )
            rows = s.scalars(
                select(_ConversationRow)
                .where(_ConversationRow.contact_id == contact_id)
                .order_by(_ConversationRow.updated_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._row_to_dto(row) for row in rows], total

    def get_or_create_for_a2a_peer(self, *, peer_magi_id: int) -> Conversation:
        """Return this MAGI's private transcript header for one A2A peer.

        A2A traffic has no human ``Contact`` and is intentionally not an
        external channel, but its durable transcript still uses the same
        ``chat_messages`` table as ordinary turns.  ``contact_id=0`` is the
        reserved system owner for these internal-only headers; the stable
        hashed ID and delivery address keep one thread per peer without
        exposing it through any contact-owned conversation path.
        """
        if peer_magi_id <= 0:
            raise ValueError("peer_magi_id must be positive")

        delivery_address = f"a2a:{peer_magi_id}"
        with self._session() as s:
            row = s.scalar(
                select(_ConversationRow).where(
                    _ConversationRow.delivery_address == delivery_address,
                    _ConversationRow.contact_id == 0,
                )
            )
            if row is None:
                row = _ConversationRow(
                    delivery_address=delivery_address,
                    contact_id=0,
                    channel="a2a",
                    title=f"A2A with MAGI {peer_magi_id}",
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
        record = Conversation(
            delivery_address=delivery_address,
            contact_id=contact_id,
            channel="tg",
        )
        record_id = self.add(record)
        created = self.get(record_id)
        if created is None:
            raise RuntimeError(f"conversation row {record_id} disappeared after insert")
        return created

    def create_task_conversation(
        self,
        *,
        contact_id: int,
        title: str,
        delivery_address: str = "",
        channel: str = "webui",
    ) -> int:
        """Create a new conversation for a scheduled task.

        Returns the new conversation id (``chat_conversations.id``)
        so the caller can stamp it onto the Task row.
        """
        record = Conversation(
            delivery_address=delivery_address,
            contact_id=contact_id,
            channel=channel,
            title=title,
        )
        return self.add(record)

    def append_messages(
        self,
        contact_id: int,
        conversation_id: int,
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
        # D.22: verify conversation ownership and channel match.
        conversation = self.get_for_owner(contact_id=contact_id, conversation_id=conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(
                f"Conversation {conversation_id} not found for contact_id {contact_id}"
            )
        if conversation.channel != channel:
            raise ChannelMismatchError(conversation.channel)

        self.touch(conversation_id=conversation_id)

        message_book = MessageBook(self._factory)
        persisted: list[Message] = []
        for sm in messages:
            record = Message(
                conversation_id=conversation_id,
                role=sm.role,
                text=sm.text,
                ts=sm.ts,
            )
            message_id = message_book.add(record)
            message = message_book.get(message_id)
            if message is None:
                raise RuntimeError(f"message row {message_id} disappeared after insert")
            persisted.append(message)
        return persisted

    def get_messages_page(
        self,
        contact_id: int,
        conversation_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
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
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if self.get_for_owner(contact_id=contact_id, conversation_id=conversation_id) is None:
            raise ConversationNotFoundError(conversation_id)
        message_book = MessageBook(self._factory)
        return message_book.list_for_conversation_page(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )

    def touch(self, *, conversation_id: int, updated_at: datetime | None = None) -> None:
        with self._session() as s:
            row = s.scalar(select(_ConversationRow).where(_ConversationRow.id == conversation_id))
            if row is None:
                return
            row.updated_at = updated_at or utcnow_naive()
            s.commit()

    def set_title_if_null(
        self,
        *,
        contact_id: int,
        conversation_id: int,
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
        now = utcnow_naive()
        with self._session() as s:
            stmt = (
                update(_ConversationRow)
                .where(
                    _ConversationRow.id == conversation_id,
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
            row = s.scalar(select(_ConversationRow).where(_ConversationRow.id == conversation_id))
            return self._row_to_dto(row) if row else None

    def set_summary(
        self,
        *,
        contact_id: int,
        conversation_id: int,
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
        now = utcnow_naive()
        with self._session() as s:
            stmt = (
                update(_ConversationRow)
                .where(
                    _ConversationRow.id == conversation_id,
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
            row = s.scalar(select(_ConversationRow).where(_ConversationRow.id == conversation_id))
            return self._row_to_dto(row) if row else None

    def set_title(
        self,
        *,
        contact_id: int,
        conversation_id: int,
        title: str | None,
        bump_updated: bool = True,
    ) -> Conversation | None:
        """Set (or clear) the conversation title, scoped to its owner.

        Unlike :meth:`set_title_if_null` (the auto-title CAS primitive),
        this is an unconditional write — ``title=None`` clears the column.
        Returns the updated DTO, or ``None`` when no matching row exists.
        """
        now = utcnow_naive()
        with self._session() as s:
            stmt = (
                update(_ConversationRow)
                .where(
                    _ConversationRow.id == conversation_id,
                    _ConversationRow.contact_id == contact_id,
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
                select(_ConversationRow).where(
                    _ConversationRow.id == conversation_id
                )
            )
            return self._row_to_dto(row) if row else None

    def delete_owned(self, *, contact_id: int, conversation_id: int) -> bool:
        """Delete one owned conversation (and its messages via FK cascade).

        Idempotent — returns ``False`` (without error) when the row is
        absent or owned by another contact, so a stale DELETE on an
        already-removed id is a no-op. ``chat_messages`` rows are removed
        by the ``ondelete="CASCADE"`` FK (``foreign_keys=ON`` is enabled
        in :mod:`magi.bus.bases.db.engine`).
        """
        with self._session() as s:
            row = s.scalar(
                select(_ConversationRow).where(
                    _ConversationRow.id == conversation_id,
                    _ConversationRow.contact_id == contact_id,
                )
            )
            if row is None:
                return False
            record_id = row.id
        return self.delete(record_id)


class MessageBook(BaseBook[_MessageRow, Message]):
    model_cls = _MessageRow
    record_cls = Message

    def __init__(self, factory, *, settings_book=None) -> None:  # type: ignore[no-untyped-def]  # noqa: ARG002
        super().__init__(factory)

    def _row_to_dto(self, row: _MessageRow) -> Message:
        conversation = row.conversation
        if conversation is None:
            raise ConversationCorruptError(
                f"message {row.id} references missing conversation row {row.conversation_row_id}"
            )
        message = Message(
            conversation_id=conversation.id,
            role=row.role,
            text=row.text,
            ts=row.ts,
            archived=row.archived,
        )
        object.__setattr__(message, "id", row.id)
        object.__setattr__(message, "created_at", row.created_at)
        object.__setattr__(message, "updated_at", row.updated_at)
        return message

    def list_for_conversation(
        self, *, conversation_id: int, include_archived: bool = False
    ) -> list[Message]:
        with self._session() as s:
            stmt = (
                select(_MessageRow)
                .join(_ConversationRow, _ConversationRow.id == _MessageRow.conversation_row_id)
                .where(_ConversationRow.id == conversation_id)
            )
            if not include_archived:
                stmt = stmt.where(_MessageRow.archived == 0)
            stmt = stmt.order_by(_MessageRow.id)
            rows = s.scalars(stmt).all()
            return [self._row_to_dto(r) for r in rows]

    def list_for_conversation_page(
        self,
        *,
        conversation_id: int,
        limit: int = 50,
        offset: int = 0,
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
        ``from __future__ import annotations`` for some dataclass
        versions.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        from sqlalchemy import func

        with self._session() as s:
            base = (
                select(_MessageRow)
                .join(_ConversationRow, _ConversationRow.id == _MessageRow.conversation_row_id)
                .where(_ConversationRow.id == conversation_id)
            )
            archived_filter = [] if include_archived else [_MessageRow.archived == 0]

            # ``offset`` counts from the newest end: the WebUI first loads
            # the current tail, then increments it to fetch older pages.
            # Select in reverse order to apply that offset correctly, then
            # reverse the bounded page before returning it to retain the
            # public oldest-first rendering contract.
            page_rows = list(
                s.scalars(
                    base.where(*archived_filter)
                    .order_by(_MessageRow.id.desc())
                    .limit(limit)
                    .offset(offset)
                ).all()
            )
            page_rows.reverse()
            total_active = (
                s.scalar(
                    select(func.count())
                    .select_from(_MessageRow)
                    .join(_ConversationRow, _ConversationRow.id == _MessageRow.conversation_row_id)
                    .where(_ConversationRow.id == conversation_id)
                    .where(_MessageRow.archived == 0)
                )
                or 0
            )
            total_all = (
                s.scalar(
                    select(func.count())
                    .select_from(_MessageRow)
                    .join(_ConversationRow, _ConversationRow.id == _MessageRow.conversation_row_id)
                    .where(_ConversationRow.id == conversation_id)
                )
                or 0
            )
            # ``_row_to_dto`` reads ``row.conversation``.  It must run while
            # the SQLAlchemy session is still open, otherwise the lazy
            # relationship raises DetachedInstanceError for every non-empty
            # page returned to the WebUI.
            page_messages = [self._row_to_dto(row) for row in page_rows]
        return (page_messages, int(total_active), int(total_all))

    def _record_to_row_values(self, record: Message, session) -> dict:
        """Map the DTO onto row columns; the conversation reference is direct."""
        if session.get(_ConversationRow, record.conversation_id) is None:
            raise ConversationNotFoundError(record.conversation_id)
        return {
            "conversation_row_id": record.conversation_id,
            "role": record.role,
            "text": record.text,
            "ts": record.ts or utcnow_naive(),
            "archived": record.archived,
        }

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
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
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
            "JOIN chat_conversations c ON c.id = m.conversation_row_id "
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
                    "SELECT c.id AS conversation_id, m.id AS message_id, m.role, m.ts, "
                    "c.title, c.channel, c.delivery_address, "
                    "snippet(chat_messages_fts, 0, '<mark>', '</mark>', "
                    "'…', 16) AS snippet, "
                    "bm25(chat_messages_fts) AS score "
                    + base
                    + " ORDER BY score LIMIT :limit OFFSET :offset"
                ).columns(ts=DateTime),
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
            if m.id == hit.message_id:
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
            if m.id == hit.message_id:
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
    "AgentMessageRole",
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
