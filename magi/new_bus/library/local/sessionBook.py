"""SessionBook + MessageBook — chat session and message transcript.

Two tables:
- ``chat_sessions``  — one row per chat session (Crockford ULID primary key)
- ``chat_messages``  — one row per persisted transcript message

Plus a SQLite-only ``chat_messages_fts`` virtual table (FTS5,
``trigram`` tokeniser) with three triggers that keep it in lockstep
with ``chat_messages.id`` / ``chat_messages.text``. The triggers are
the same ones alembic migration 0001 lays down for the old bus; we
re-install them here so a new_bus-only deployment (or a test that
constructs a fresh SQLite file via ``EngineFactory.create_all``) gets
a working full-text index out of the box. ``ensure_fts`` is idempotent
— ``CREATE ... IF NOT EXISTS`` makes it safe to run repeatedly, and
safe to coexist with the old bus's migration (the old bus ran alembic
0001 first; the new_bus's bootstrap second; the second run is a no-op).

Schema mirrors the old bus's ``chat_sessions`` + ``chat_messages``
tables; the row shapes are identical, so the FTS5 index is shared
across both code paths transparently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    delivery_address: str
    uid: int
    channel: str
    title: str | None = None
    active_tail_count: int = 20
    last_compaction_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    session_id: str
    message_id: str
    role: str
    text: str
    ts: str
    archived: int = 0
    content_blocks: list[dict[str, Any]] | None = None
    run_id: str | None = None
    llm_attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One row of chat-history FTS5 search output.

    Carries the snippet (with literal ``<mark>...</mark>`` tags
    already inserted by ``snippet(chat_messages_fts, ...)``) and
    the bm25 score (lower = better). ``session_id`` / ``message_id``
    let the caller resolve the hit back to its
    :class:`Message` / :class:`Session` row.
    """

    session_id: str
    message_id: str
    role: str
    ts: str
    snippet: str
    score: float
    channel: str
    title: str | None = None
    delivery_address: str | None = None


class SearchUnavailable(RuntimeError):
    """SQLite in this deployment was built without the FTS table.

    The FTS5 virtual table is a side artefact of
    :func:`install_session_fts_schema` (or alembic migration 0001 in
    the old bus). When neither has been run — typically because the
    SQLite build lacks FTS5, or because the bootstrap ran before the
    FTS installer — ``MessageBook.search`` raises this instead of
    returning empty results, so callers can surface a 503 rather than
    a silently-empty search box.
    """


@dataclass(frozen=True, slots=True)
class ResolvedHit:
    """A search hit after cross-contact validation + context fetch.

    Returned by :meth:`MessageBook.resolve_hit` — the single helper
    that closes the gap between the FTS row (which only carries a
    ``message_id``, a snippet, and a bm25 score) and the full picture
    the renderer / API consumer needs.

    ``session`` is the owning session header. Always already
    uid-checked — if the hit pointed at another operator's session,
    ``resolve_hit`` returns ``None`` instead of a partial envelope.

    ``is_archived`` is True when the hit landed on a row that
    auto-compaction rolled out (``chat_messages.archived == 1``).
    Archived hits carry no clean neighbour, so ``messages_with_hit``
    is empty and ``hit_position`` is ``-1`` — the caller emits the
    snippet only (the LLM tool renders this as ``(archived) snippet:
    ...``; the future ``/api/chat/search`` HTTP endpoint will mirror
    the same shape).

    ``messages_with_hit`` is the **active** subset of the session
    messages, sliced ±``context_n`` around the hit. Length is
    ``2 * context_n + 1`` in the middle of a long session, shorter
    near session boundaries, and zero when ``is_archived`` or
    ``context_n == 0``.

    ``hit_position`` is the index of the hit inside
    ``messages_with_hit``. The renderer re-attaches the snippet's
    ``<mark>`` highlighting at this position (so the LLM sees where
    in the message the FTS match landed, not just the surrounding
    text).
    """

    session: Session
    hit: SearchHit
    is_archived: bool
    messages_with_hit: list[Message]
    hit_position: int


# -- internal ORM --------------------------------------------------------


class _SessionRow(Base):
    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    delivery_address: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active_tail_count: Mapped[int] = mapped_column(
        Integer, default=20, nullable=False
    )
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)


class _MessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_chat_messages_session_archived", "session_id", "archived", "id"),
        UniqueConstraint(
            "session_id", "message_id", name="uq_chat_messages_session_msg"
        ),
    )


# -- Books ---------------------------------------------------------------


class SessionBook(BaseBook[_SessionRow, Session]):
    model_cls = _SessionRow
    dto_cls = Session

    def get(self, *, session_id: str) -> Session | None:
        with self._session() as s:
            row = s.scalar(
                select(_SessionRow).where(_SessionRow.session_id == session_id)
            )
            return self._row_to_dto(row) if row else None

    def get_for_owner(self, *, uid: int, session_id: str) -> Session | None:
        """``get`` with cross-contact defence-in-depth.

        The old bus's :meth:`SessionService.get` accepted ``(uid,
        session_id)`` and silently dropped rows that didn't match
        the caller's uid; the new_bus's :meth:`get` accepts
        ``session_id`` only, which would let a caller guess another
        operator's ``session_id`` and pull its header back. The
        FTS5 search path is already scoped by ``WHERE s.uid = :uid``
        inside the JOIN, so a tool that only goes through
        :meth:`MessageBook.search` is safe — but the moment any
        caller resolves a hit back through ``sessions_book.get``
        (e.g. to render a context slice, or for the future
        ``/api/chat/search`` HTTP endpoint), they need the uid
        check to live somewhere.

        This method is the single home for that check: returns the
        session **only** if ``uid`` owns it, otherwise ``None``.
        Both the LLM tool and the HTTP API route through here, so
        the cross-contact defence lives in one place rather than
        being re-implemented (and forgotten) at every call site.
        """
        session = self.get(session_id=session_id)
        if session is None:
            return None
        if session.uid != uid:
            return None
        return session

    def list_for_owner(self, *, uid: int) -> list[Session]:
        with self._session() as s:
            rows = s.scalars(
                select(_SessionRow)
                .where(_SessionRow.uid == uid)
                .order_by(_SessionRow.updated_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, session_id: str, delivery_address: str, uid: int,
            channel: str, title: str | None = None,
            created_at: str = "", updated_at: str = "") -> Session:
        with self._session() as s:
            row = _SessionRow(
                session_id=session_id,
                delivery_address=delivery_address,
                uid=uid,
                channel=channel,
                title=title,
                created_at=created_at,
                updated_at=updated_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def touch(self, *, session_id: str, updated_at: str) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_SessionRow).where(_SessionRow.session_id == session_id)
            )
            if row is None:
                return
            row.updated_at = updated_at
            s.commit()

    def set_title_if_null(
        self,
        *,
        uid: int,
        session_id: str,
        title: str,
        bump_updated: bool = True,
    ) -> "Session | None":
        """[claude, 2026-08-08] CAS-style title set — only writes if currently NULL.

        Required by :func:`magi.agent.auto_title.request_session_title`
        new_bus path. Mirrors old
        :meth:`magi.bus.jobs.services.session.SessionService.set_title_if_null`:
        scope by ``(uid, session_id)`` (cross-contact defence), only set
        when existing ``title`` is NULL, optionally bump ``updated_at``.

        Returns the updated :class:`Session` on success (lost the race
        to another writer that already set a title), or ``None`` when
        no matching row was found.
        """
        from magi.new_bus.db.base import utcnow_naive

        now = utcnow_naive().isoformat() + "Z"
        with self._session() as s:
            stmt = (
                update(_SessionRow)
                .where(
                    _SessionRow.session_id == session_id,
                    _SessionRow.uid == uid,
                    _SessionRow.title.is_(None),
                )
                .values(title=title)
            )
            if bump_updated:
                stmt = stmt.values(updated_at=now)
            result = s.execute(stmt)
            if result.rowcount == 0:
                s.rollback()
                return None
            s.commit()
            row = s.scalar(
                select(_SessionRow).where(_SessionRow.session_id == session_id)
            )
            return self._row_to_dto(row) if row else None


class MessageBook(BaseBook[_MessageRow, Message]):
    model_cls = _MessageRow
    dto_cls = Message

    def get(self, *, message_id: int) -> Message | None:
        with self._session() as s:
            row = s.scalar(select(_MessageRow).where(_MessageRow.id == message_id))
            return self._row_to_dto(row) if row else None

    def list_for_session(self, *, session_id: str,
                         include_archived: bool = False) -> list[Message]:
        with self._session() as s:
            stmt = select(_MessageRow).where(_MessageRow.session_id == session_id)
            if not include_archived:
                stmt = stmt.where(_MessageRow.archived == 0)
            stmt = stmt.order_by(_MessageRow.id)
            rows = s.scalars(stmt).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, session_id: str, message_id: str, role: str, text: str,
            ts: str, content_blocks: list[dict[str, Any]] | None = None,
            run_id: str | None = None,
            llm_attempt_id: str | None = None) -> Message:
        with self._session() as s:
            row = _MessageRow(
                session_id=session_id,
                message_id=message_id,
                role=role,
                text=text,
                ts=ts,
                content_blocks=content_blocks,
                run_id=run_id,
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
        install_session_fts_schema(self._factory.engine)

    def search(
        self,
        *,
        uid: int,
        q: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SearchHit], int]:
        """Full-text search across ``chat_messages`` rows owned by ``uid``.

        Scoping: ``WHERE s.uid = :uid`` is part of the join, not a
        post-filter — so the bm25 ranking is computed on the
        contact's own corpus, never on someone else's. ``q`` is
        whitespace-tokenised into quoted ``"<token>"`` substrings,
        mirroring the old bus's sanitiser. The MATCH expression uses
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
            "JOIN chat_sessions s ON s.session_id = m.session_id "
            "WHERE chat_messages_fts MATCH :match AND s.uid = :uid"
        )
        with self._session() as s:
            available = s.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='chat_messages_fts'"
                )
            ).first()
            if available is None:
                raise SearchUnavailable(
                    "Full-text search is not available in this SQLite build"
                )
            total = s.execute(
                text("SELECT COUNT(*) " + base),
                {"match": match, "uid": uid},
            ).scalar_one()
            rows = s.execute(
                text(
                    "SELECT m.session_id, m.message_id, m.role, m.ts, "
                    "s.title, s.channel, s.delivery_address, "
                    "snippet(chat_messages_fts, 0, '<mark>', '</mark>', "
                    "'…', 16) AS snippet, "
                    "bm25(chat_messages_fts) AS score "
                    + base
                    + " ORDER BY score LIMIT :limit OFFSET :offset"
                ),
                {"match": match, "uid": uid, "limit": limit, "offset": offset},
            ).fetchall()

        return [
            SearchHit(
                session_id=row.session_id,
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
        uid: int,
        hit: SearchHit,
        context_n: int,
        sessions_book: "SessionBook",
    ) -> ResolvedHit | None:
        """Resolve a search hit to its full context.

        This is the shared **business logic** every consumer of
        search needs in common — the LLM tool and the future
        ``/api/chat/search`` HTTP endpoint both need to:

          1. Validate the hit's session belongs to ``uid`` (the
             FTS query is already scoped by ``s.uid = :uid`` in
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

        ``sessions_book`` is passed explicitly rather than held
        on ``self`` because ``MessageBook`` doesn't otherwise
        need a reference to its sibling — keeping the Book's
        dependency surface minimal. Bootstrap wires both Books
        off the same factory and the caller always has both
        handy (via ``bus.messages_book`` / ``bus.sessions_book``).

        Returns ``None`` when:
          - the hit's session doesn't belong to ``uid`` (cross-
            contact leak attempt; ``get_for_owner`` returned None)
          - the hit row was deleted between FTS read and now
            (race)

        In both cases the caller emits a generic
        "session no longer accessible" hint instead of leaking
        the row's metadata.

        For archived hits or ``context_n == 0``, returns a
        ``ResolvedHit`` with ``is_archived=True`` (or
        ``messages_with_hit=[]``) and ``hit_position=-1``.
        """
        if context_n < 0:
            context_n = 0

        session = sessions_book.get_for_owner(
            uid=uid, session_id=hit.session_id,
        )
        if session is None:
            return None

        # One fetch covers both branches: active hits (the common
        # case) and archived hits (rare — auto-compaction only
        # flips the flag, never reorders rows). The combined list
        # is sorted by row id which is monotonic per session.
        messages = self.list_for_session(
            session_id=hit.session_id, include_archived=True,
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
                session=session, hit=hit, is_archived=True,
                messages_with_hit=[], hit_position=-1,
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
                session=session, hit=hit, is_archived=True,
                messages_with_hit=[], hit_position=-1,
            )

        lo = max(0, active_idx - context_n)
        hi = min(len(active_msgs), active_idx + context_n + 1)
        return ResolvedHit(
            session=session, hit=hit, is_archived=False,
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
    # Sync triggers — same pattern as alembic migration 0001.
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


def install_session_fts_schema(engine) -> None:
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
    "Session",
    "Message",
    "SearchHit",
    "SearchUnavailable",
    "ResolvedHit",
    "SessionBook",
    "MessageBook",
    "install_session_fts_schema",
    "_SessionRow",
    "_MessageRow",
]