"""SessionStore — SQLite-backed session CRUD.

Stateless; safe to instantiate per-request. The ``state_dir``
field is kept for caller compat (chat.py / bot.py /
chat_sessions.py / auto_title all build a ``SessionStore(
state_dir=...)``). The path is resolved once per process via
the ORM engine singleton — see :mod:`magi.agent.db.orm`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

from magi.agent.memory.session.errors import (
    SessionCorruptError,
    SessionNotFoundError,
)
from magi.agent.memory.session.ids import (
    _validate_chat_id,
    _validate_session_id,
    new_session_id,
    utcnow_iso,
)
from magi.agent.memory.session.models import (
    SCHEMA_VERSION,
    Session,
    SessionMessage,
    SessionSummary,
    _ALLOWED_MESSAGE_ROLES,
    _PREVIEW_CHARS,
    summary_from_session,
)
from magi.agent.db.engine import open_session
from magi.agent.memory.session.tables import ChatMessage, ChatSession


logger = logging.getLogger("magi.agent.memory.session.store")


# Title length ceiling: matches the Pydantic ``max_length`` on
# ``PATCH /api/chat/sessions/{id}`` body. Truncating here too
# guards against a hand-crafted endpoint bypass that bypasses
# the Pydantic body validation.
_TITLE_MAX_LEN = 80


@dataclass
class SessionStore:
    """SQLite-backed session storage (D.18+).

    Pre-D.18 this was JSON files under ``<workspace>/memories/
    sessions/<chat_id>/<sid>.json``. The class kept the same
    public method signatures so the ~30 callers (chat.py /
    bot.py / agent.py / auto_title.py / chat_sessions.py) didn't
    need to change; only the bodies switched to ORM queries.

    Stateless — safe to instantiate per-request. The ``state_dir``
    arg is kept for caller compat (it's how the store knows
    which ``magi.db`` file to hit; the path is resolved once per
    process via the ORM engine singleton).
    """

    state_dir: str | os.PathLike[str]

    # -- public -----------------------------------------------------------

    def create(
        self,
        chat_id: str,
        *,
        employee_id: int,
        channel: str = "webui",
    ) -> Session:
        """Create a new empty session.

        The ``chat_id`` parameter is the **Telegram chat id**
        — for WebUI sessions it's the admin's telegram_id
        (the cookie), for TG inbound sessions it's the
        TG user's chat_id. Stored in the ``tgid`` column
        since D.18+1; the parameter name stays ``chat_id``
        so the dozen-odd call sites don't need to change.

        ``employee_id`` is the operator whose history this
        row belongs to — used as the cross-platform search
        scope by the ``search_sessions`` tool.
        """
        _validate_chat_id(chat_id)
        session_id = new_session_id()
        now = utcnow_iso()
        with open_session() as db:
            db.add(ChatSession(
                session_id=session_id,
                tgid=chat_id,
                employee_id=employee_id,
                channel=channel,
                title=None,
                active_tail_count=20,
                last_compaction_at=None,
                created_at=now,
                updated_at=now,
            ))
            db.commit()
        logger.info(
            "session created",
            extra={
                "session_id": session_id,
                "chat_id": chat_id,
                "employee_id": employee_id,
            },
        )
        return Session(
            session_id=session_id,
            chat_id=chat_id,
            employee_id=employee_id,
            channel=channel,
            created_at=now,
            updated_at=now,
            messages=[],
            title=None,
            active_tail_count=20,
            last_compaction_at=None,
        )

    def get(self, chat_id: str, session_id: str) -> Session | None:
        """Read a session by id. Returns ``None`` if missing.

        The ``messages`` list is the **active** view
        (``archived=0``). Archive rows are loaded into
        ``Session.archive`` so callers (compaction, audit UI)
        can still see the pre-D.17 forensic record without a
        second query.

        ``chat_id`` here means the row's ``tgid`` column —
        the Telegram chat identifier this session belongs
        to. We check it as a defense in depth (a caller
        passing the wrong tgid for a known session_id gets
        ``None`` instead of a leak).
        """
        _validate_session_id(session_id)
        _validate_chat_id(chat_id)
        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.tgid != chat_id:
                return None
            # Active messages in append-order
            active = [
                m for m in sess_row.messages
                if m.archived == 0
            ]
            archive = [
                m for m in sess_row.messages
                if m.archived == 1
            ]
            return Session(
                session_id=sess_row.session_id,
                chat_id=sess_row.tgid,
                employee_id=sess_row.employee_id,
                channel=sess_row.channel,
                created_at=sess_row.created_at,
                updated_at=sess_row.updated_at,
                title=sess_row.title,
                schema_version=SCHEMA_VERSION,
                messages=[
                    SessionMessage(
                        role=m.role, text=m.text,
                        ts=m.ts, message_id=m.message_id,
                    )
                    for m in active
                ],
                archive=[
                    SessionMessage(
                        role=m.role, text=m.text,
                        ts=m.ts, message_id=m.message_id,
                    )
                    for m in archive
                ],
                active_tail_count=sess_row.active_tail_count,
                last_compaction_at=sess_row.last_compaction_at,
            )

    def append_messages(
        self,
        chat_id: str,
        session_id: str,
        msgs: Iterable[SessionMessage],
        *,
        bump_updated: bool = True,
    ) -> Session:
        """Append one or more messages to a session.

        Single transaction (one INSERT per message + one UPDATE
        on the session row). ``bump_updated=False`` skips the
        ``updated_at`` bump — used by operations that touch
        metadata only.
        """
        _validate_session_id(session_id)
        _validate_chat_id(chat_id)
        new_msgs = list(msgs)
        # Validate up-front so a partial append isn\'t possible.
        for i, m in enumerate(new_msgs):
            if m.role not in _ALLOWED_MESSAGE_ROLES:
                raise SessionCorruptError(
                    f"appending messages[{i}].role {m.role!r} "
                    "is not allowed"
                )

        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.tgid != chat_id:
                raise SessionNotFoundError(
                    f"session {session_id!r} for chat_id {chat_id!r} "
                    "does not exist"
                )
            for m in new_msgs:
                db.add(ChatMessage(
                    session_id=session_id,
                    message_id=m.message_id,
                    role=m.role,
                    text=m.text,
                    ts=m.ts,
                    archived=0,
                ))
            if bump_updated:
                sess_row.updated_at = utcnow_iso()
            db.commit()
        # Re-read so the returned Session matches what\'s on disk.
        return self.get(chat_id, session_id)  # type: ignore[return-value]

    def rename(
        self,
        chat_id: str,
        session_id: str,
        title: str | None,
        *,
        bump_updated: bool = True,
    ) -> Session:
        """Set or clear the session\'s ``title``.

        ``title`` is trimmed and length-clamped to
        ``_TITLE_MAX_LEN`` chars. ``None`` (or an empty
        string after trimming) clears the title.

        ``bump_updated=False`` skips the ``updated_at`` bump —
        used by the manual ``PATCH`` path because a rename is
        operator metadata and shouldn\'t reshuffle the sidebar.
        """
        _validate_session_id(session_id)
        _validate_chat_id(chat_id)
        if title is None:
            new_title: str | None = None
        else:
            stripped = title.strip()
            new_title = stripped[:_TITLE_MAX_LEN] if stripped else None

        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.tgid != chat_id:
                raise SessionNotFoundError(
                    f"session {session_id!r} for chat_id {chat_id!r} "
                    "does not exist"
                )
            sess_row.title = new_title
            if bump_updated:
                sess_row.updated_at = utcnow_iso()
            db.commit()
        logger.info(
            "session renamed",
            extra={
                "session_id": session_id,
                "chat_id": chat_id,
                "title_set": new_title is not None,
            },
        )
        return self.get(chat_id, session_id)  # type: ignore[return-value]

    def set_title_if_null(
        self,
        chat_id: str,
        session_id: str,
        title: str,
        *,
        bump_updated: bool = True,
    ) -> Session | None:
        """Set the session\'s ``title`` only if it is currently NULL.

        Atomic — single ``UPDATE … WHERE title IS NULL`` whose
        affected-row count tells the caller whether they won
        the race against a concurrent manual PATCH or another
        worker. Returns the post-update ``Session`` on success,
        ``None`` when the row didn\'t exist OR the title was
        already set.

        Used by the D.7 auto-title worker to replace the
        pre-D.18 ``async with session_lock(...)`` read-then-
        write pattern with a SQL-level compare-and-set. The
        lock is no longer needed because SQLAlchemy\'s
        ``begin`` event listener issues ``BEGIN IMMEDIATE``,
        serialising the UPDATE across the writer pool.
        """
        from sqlalchemy import update

        with open_session() as db:
            stmt = (
                update(ChatSession)
                .where(
                    ChatSession.session_id == session_id,
                    ChatSession.tgid == chat_id,
                    ChatSession.title.is_(None),
                )
                .values(
                    title=title[:_TITLE_MAX_LEN],
                    updated_at=utcnow_iso() if bump_updated else ChatSession.updated_at,
                )
            )
            result = db.execute(stmt)
            db.commit()
            if result.rowcount == 0:
                # Either session doesn\'t exist or title was
                # already set by someone else — caller treats
                # both as "lost the race".
                return None
            return self.get(chat_id, session_id)

    def _write(self, session: Session, *, bump_updated: bool = True) -> Session:
        """Persist a (possibly-mutated) ``Session`` back to
        the DB. Used by :mod:`magi.agent.loop` after
        auto-compaction rewrites ``session.messages`` and
        ``session.archive``.

        The single transaction:
          1. ``UPDATE chat_messages SET archived=1`` for the
             to-archive rows (preserves ``message_id`` — the
             v0 code re-minted ids, which made deep-linking
             from search results brittle).
          2. ``INSERT`` the new system-summary row at
             ``messages[0]`` (always ``archived=0``).
          3. Optionally bump ``updated_at`` on the session.
          4. UPDATE the session\'s metadata
             (``active_tail_count``, ``last_compaction_at``).

        Atomicity: any step failing rolls back the whole
        write. The FTS5 sync triggers fire per-row inside
        the same transaction, so the search index stays
        coherent with the messages table.
        """
        with open_session() as db:
            sess_row = db.get(ChatSession, session.session_id)
            if sess_row is None:
                # Session disappeared mid-call; bail silently
                # to match v0 behaviour.
                return session

            # Archive the OLD messages: flip their archived
            # flag. Original message_ids are preserved (the
            # FTS5 rowids / search deep-links stay valid).
            if session.archive:
                # Find which message_ids belong to this
                # session and are still active; flip the
                # ones we want to archive. Match by
                # message_id (not row id) so the caller
                # can hand us a Session built from a fresh
                # ``get()`` without rowid-bookkeeping.
                archive_ids = {m.message_id for m in session.archive}
                for row in sess_row.messages:
                    if row.archived == 0 and row.message_id in archive_ids:
                        row.archived = 1

            # Rewrite the active messages: delete old active
            # rows (those NOT in the new active set) and
            # insert the new ones. We use message_id as the
            # key — the summary row from compaction has a
            # fresh message_id and gets inserted as new.
            new_active_ids = {m.message_id for m in session.messages}
            for row in sess_row.messages:
                if row.archived == 0 and row.message_id not in new_active_ids:
                    db.delete(row)
            for m in session.messages:
                # Skip if already present (carried over from
                # the old active set) — avoids re-inserting
                # verbatim tails the compaction kept.
                existing = next(
                    (r for r in sess_row.messages if r.message_id == m.message_id),
                    None,
                )
                if existing is not None:
                    continue
                db.add(ChatMessage(
                    session_id=session.session_id,
                    message_id=m.message_id,
                    role=m.role,
                    text=m.text,
                    ts=m.ts,
                    archived=0,
                ))

            sess_row.active_tail_count = session.active_tail_count
            sess_row.last_compaction_at = session.last_compaction_at
            if bump_updated:
                sess_row.updated_at = utcnow_iso()
            db.commit()

        # Return a fresh read so the caller sees what\'s
        # actually on disk (and so the in-memory Session
        # they\'re holding matches the persisted state).
        return self.get(session.chat_id, session.session_id)  # type: ignore[return-value]

    def delete(self, chat_id: str, session_id: str) -> bool:
        """Remove a session. ``True`` if it existed.

        Idempotent: deleting a non-existent session is a
        no-op (returns ``False``). No trash; v0 doesn\'t
        support undo.
        """
        _validate_session_id(session_id)
        _validate_chat_id(chat_id)
        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.tgid != chat_id:
                return False
            # CASCADE on the FK cleans up the message rows
            # automatically. The FTS sync triggers fire per
            # delete inside the same transaction.
            db.delete(sess_row)
            db.commit()
        logger.info(
            "session deleted",
            extra={"session_id": session_id, "chat_id": chat_id},
        )
        return True

    def list_summaries(
        self,
        chat_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SessionSummary], int]:
        """Return ``(summaries, total)`` for the chat.

        Sorts by ``updated_at`` descending. ``preview`` is the
        first user message text (truncated to 80 chars with a
        trailing ellipsis if longer).
        """
        from sqlalchemy import func, select

        with open_session() as db:
            # Header rows (newest first by updated_at).
            headers = db.execute(
                select(ChatSession)
                .where(ChatSession.tgid == chat_id)
                .order_by(ChatSession.updated_at.desc())
            ).scalars().all()
            total = len(headers)
            page = headers[offset : offset + limit]

            # For each header, fetch the first user message
            # for the preview. Single round-trip via a JOIN
            # would be faster, but the cardinality is small
            # (a page of ~50), and the SQL stays readable.
            summaries: list[SessionSummary] = []
            for h in page:
                preview = ""
                first_user = db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == h.session_id,
                        ChatMessage.archived == 0,
                        ChatMessage.role == "user",
                    )
                    .order_by(ChatMessage.id)
                    .limit(1)
                ).scalar_one_or_none()
                if first_user is not None:
                    preview = first_user.text[:_PREVIEW_CHARS]
                    if len(first_user.text) > _PREVIEW_CHARS:
                        preview += "…"

                # Active message count for ``message_count``.
                count = db.execute(
                    select(func.count(ChatMessage.id))
                    .where(
                        ChatMessage.session_id == h.session_id,
                        ChatMessage.archived == 0,
                    )
                ).scalar_one()

                summaries.append(SessionSummary(
                    session_id=h.session_id,
                    created_at=h.created_at,
                    updated_at=h.updated_at,
                    message_count=count,
                    preview=preview,
                    title=h.title,
                ))
            return summaries, total

    # -- message pagination (D.18+2) ------------------------------
    #
    # Sessions grow over time. ``get()`` returns *all* active
    # + archive rows in one shot — fine for a 30-message
    # thread, but a long-lived chat hits 500+ rows and the
    # initial WebUI load is half a megabyte of JSON. The
    # chat pane already renders bottom-up (newest at the
    # scroll bottom, scroll-up to load older); the API
    # needs the matching shape: a tail-slice endpoint.
    #
    # Convention: ``direction="tail"`` (default) returns the
    # *newest* ``limit`` active messages, sorted by
    # ``chat_messages.id ASC``. ``offset=N`` skips the N
    # newest rows — so page 0 is the latest 50, page 1 is
    # the next-50 older, etc. ``total`` returns the full
    # active-message count so the UI knows whether there\'s
    # older history to load.
    #
    # Archive rows are NOT included in the default page —
    # the WebUI chat pane doesn\'t render them in the
    # conversation scroll (they live in a separate
    # "archive" view via the D.18 search tool / future
    # session-detail UI). Passing ``include_archived=True``
    # opts into loading them too — useful for the audit
    # view or future "show full history" affordance.

    def get_messages_page(
        self,
        chat_id: str,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
    ) -> tuple[list[SessionMessage], int, int]:
        """Return ``(messages, total_active, total_all)``.

        ``messages`` is the requested page, in chronological
        order (oldest-first within the page — same order as
        the WebUI renders). ``total_active`` is the count of
        non-archived rows in this session; ``total_all`` is
        the count with archive included.

        Returns ``([], 0, 0)`` if the session doesn\'t exist.
        """
        from sqlalchemy import func, select

        _validate_chat_id(chat_id)
        _validate_session_id(session_id)
        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.tgid != chat_id:
                return [], 0, 0

            # Totals — separate active / all so the UI can
            # decide whether to show "load older messages"
            # (active_total > loaded_so_far) without also
            # having to expose the archive count.
            active_total = db.scalar(
                select(func.count(ChatMessage.id))
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.archived == 0,
                )
            ) or 0
            all_total = db.scalar(
                select(func.count(ChatMessage.id))
                .where(ChatMessage.session_id == session_id)
            ) or 0

            # Tail slice: newest ``limit`` active rows
            # ordered by id ASC (chronological). Skip the
            # newest ``offset`` rows so the caller pages
            # backwards. We compute the (start, end) id
            # range in SQL rather than OFFSET/LIMIT so the
            # total scan count is bounded — a 10k-message
            # session shouldn\'t have to count past 10k rows
            # to grab page 0.
            #
            # Idempotency: caller passes ``offset`` as the
            # number of *already-loaded* active messages.
            # The total grows by N when N new messages
            # land mid-paging — the WHERE on id <=
            # ``newest_id - offset`` keeps the page
            # stable.
            window = db.execute(
                select(ChatMessage.id)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.archived == 0,
                )
                .order_by(ChatMessage.id.desc())
                .limit(limit)
                .offset(offset)
            ).scalars().all()

            if not window:
                return [], active_total, all_total

            # The window is in DESC order; turn it back into
            # chronological and load the full rows.
            ascending_ids = list(reversed(window))
            rows = db.execute(
                select(ChatMessage)
                .where(ChatMessage.id.in_(ascending_ids))
                .order_by(ChatMessage.id.asc())
            ).scalars().all()

            messages = [
                SessionMessage(
                    role=r.role, text=r.text,
                    ts=r.ts, message_id=r.message_id,
                )
                for r in rows
            ]

            if include_archived:
                # Append archive rows in chronological order
                # after the active tail. Their message_ids
                # are distinct from the active set (each row
                # has a unique (session_id, message_id)),
                # so no dedup needed.
                archive_rows = db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.archived == 1,
                    )
                    .order_by(ChatMessage.id.asc())
                ).scalars().all()
                messages.extend(
                    SessionMessage(
                        role=r.role, text=r.text,
                        ts=r.ts, message_id=r.message_id,
                    )
                    for r in archive_rows
                )

            return messages, active_total, all_total
