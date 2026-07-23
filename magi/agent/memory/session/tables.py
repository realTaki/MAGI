"""SQLAlchemy ORM tables for chat sessions.

Owns the on-disk shape of the session data
(``chat_sessions`` + ``chat_messages``). The in-memory
session dataclasses (:class:`Session` / :class:`SessionMessage`
in :mod:`magi.agent.memory.session.models`) are the public type
the rest of the codebase uses; these ORM classes are the
storage layer underneath.

Why these tables live with sessions, not with the rest
of the ORM tables in :mod:`magi.agent.db.models_*`:

  - They're tightly coupled to the :class:`SessionStore`
    contract (the indexes, the FTS5 sync triggers, the
    ``(session_id, message_id)`` uniqueness all serve
    the store's CRUD operations).
  - The dataclass surface and the SQL surface evolve
    together; keeping them in the same package means a
    schema change touches one review boundary.
  - The "session" concept is end-to-end owned here —
    the public dataclass, the store, the migrations,
    and now the tables.

Both tables register on the shared
:class:`magi.agent.db.base.Base` so :func:`magi.agent.db.engine.init_orm`
picks them up via the eager import in ``init_orm``. The
tables MUST be importable before ``create_all`` runs.
"""

from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


# ────────────────────────────────────────────────────────────────── #
# Chat sessions — D.18: replaces the per-session JSON files under
# `<workspace>/memories/sessions/<tgid>/<sid>.json` with two
# rows tables. ``chat_sessions`` is the header; ``chat_messages`` is
# the active + archived message log, where ``archived=0`` is the
# LLM-facing "active" view (compressed) and ``archived=1`` is the
# forensic archive (D.17's append-only log).
#
# The session row is keyed by the 26-char ULID ``session_id`` so
# callers that already hold an id (chat.py / bot.py / agent.py) can
# stay on their existing keys without translation.
# ────────────────────────────────────────────────────────────────── #


class ChatSession(Base):
    """A chat session header.

    The body of the session (messages + archive) lives in
    :class:`ChatMessage` and is loaded on demand by
    :class:`magi.agent.memory.session.SessionStore.get`.
    """

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    # ``tgid`` is the Telegram chat identifier. For WebUI
    # sessions the value is the admin's telegram_id (the
    # cookie); for TG inbound sessions it's the TG user's
    # tgid. The column is specifically the **Telegram**
    # chat id — not a generic "tgid" — because future IM
    # platforms (Slack, WeChat, etc.) will each have their
    # own identifier scheme and we don't want to overload one
    # column with three different semantics. When a non-TG
    # channel lands, the schema will gain a sibling column
    # (e.g. ``slack_im_id``) or a generic
    # ``(platform, external_id)`` pair; the search scope
    # stays on ``uid`` either way.
    #
    # Indexed so the per-channel "list my conversations"
    # endpoint (``GET /api/chat/sessions``) is a single
    # range scan per ``tgid``.
    tgid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # The employee operator whose history this row belongs
    # to. This is the search-scope key (D.18+1): an admin
    # searching with the ``search_sessions`` tool sees
    # every session whose ``uid`` matches their
    # own — webui, TG, and (in future) any other channel,
    # unified. ``tgid`` is a per-channel row identifier;
    # ``uid`` is the cross-channel identity.
    uid: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # D.7 — operator-set or auto-titled. ``null`` until either
    # has run. Bounded to 80 chars at the Pydantic boundary.
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # D.17 — snapshot of ``system.compact_keep_recent`` at the
    # last compaction pass. Pure audit trail; the next
    # compaction reads the live setting.
    active_tail_count: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    # Indexed so ``list_summaries`` ``ORDER BY updated_at DESC`` is
    # a backward index walk per ``tgid``.
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Read-only back-reference; routes never mutate via this
    # collection. ``cascade="all, delete-orphan"`` so a session
    # delete also clears its message rows.
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        viewonly=True,
        order_by="ChatMessage.id",
    )

    def __repr__(self) -> str:
        return (
            f"ChatSession(session_id={self.session_id}, "
            f"tgid={self.tgid}, title={self.title!r})"
        )


class ChatMessage(Base):
    """A single chat message, active or archived.

    Active rows (``archived=0``) are what the LLM sees in the next
    turn; archived rows (``archived=1``) are the pre-compaction
    history that ``_maybe_compact`` rolled out of ``active`` so
    the LLM's context window isn't blown by a long chat. Both are
    indexed by the same FTS5 virtual table (D.18 search) — search
    results span the whole conversation, not just the active tail.
    """

    __tablename__ = "chat_messages"

    # Autoincrement so the FTS5 ``rowid`` mapping is stable.
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Caller-supplied ULID (the same id used in D.6–D.17) so the
    # JSON-to-SQLite migration can carry the id across without
    # re-minting, and so chat_history rows can be deep-linked.
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # ISO UTC string (matches the JSON format); not DateTime so we
    # don't need tz-aware handling in code that already passes the
    # string through.
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    # 0 = active (LLM sees), 1 = archived (compressed out).
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        # Primary lookup pattern: "give me the active tail of
        # session X ordered by append-order".
        Index(
            "ix_chat_messages_session_archived",
            "session_id",
            "archived",
            "id",
        ),
        # Uniqueness on (session, message_id) lets the migration
        # importer do ``INSERT OR IGNORE`` without re-checking
        # for partial duplicates.
        UniqueConstraint(
            "session_id",
            "message_id",
            name="uq_chat_messages_session_msg",
        ),
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages", viewonly=True)

    def __repr__(self) -> str:
        flag = "A" if self.archived else "·"
        return (
            f"ChatMessage({flag} id={self.id} session={self.session_id} "
            f"role={self.role} ts={self.ts})"
        )