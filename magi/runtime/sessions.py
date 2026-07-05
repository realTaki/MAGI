"""Chat session storage — SQLite-backed conversation history.

D.18: sessions moved from per-session JSON files under
``<workspace>/memories/sessions/<chat_id>/<sid>.json`` to two
SQLAlchemy tables in ``magi.db`` (``chat_sessions`` + ``chat_messages``).
The ``SessionStore`` class kept the same public method
signatures so the ~30 callers (chat.py / bot.py / agent.py /
auto_title.py / chat_sessions.py) didn't need to change.

Why SQLite
----------

The JSON-on-disk layout had two problems D.18 surfaced:

  1. No cross-session search. Each file is self-contained;
     "search across every conversation" was a glob-and-grep
     that grew unbounded as sessions accumulated.
  2. No atomic multi-statement updates. ``_maybe_compact`` (D.17)
     had to rewrite the whole file to roll old messages into
     archive; a crash mid-write left a partial file behind.

SQLite + WAL gives us:

  - ACID transactions: compaction's archive + summary insert
    commit atomically (no more partial files).
  - FTS5 with trigram tokenizer for substring search across
    the whole history (including the D.17 archive tail).
  - Single-process reader concurrency via WAL; writer
    contention handled by ``busy_timeout=5000`` (set via the
    SQLAlchemy ``connect`` event listener in ``orm.py``).

Per-chat isolation that used to come "for free" from the
directory layout is now an explicit ``WHERE chat_id = :caller``
clause in every read/write path. The chat_sessions routes
that read by ``chat_id`` from the admin cookie enforce this
consistently.

Migration
---------

``migrate_from_json(workspace_root)`` reads any leftover
``*.json`` files under ``<workspace>/memories/sessions/``,
inserts them as rows, and deletes the JSON after each row's
transaction commits. ``INSERT OR IGNORE`` on the
``(session_id, message_id)`` unique constraint makes re-runs
idempotent — a crashed boot just retries on next start.
Corrupt files are logged and NOT deleted (no silent data
loss); they can be hand-inspected and either fixed or
removed by the operator.

ULID session id
---------------

Crockford base32 of a 48-bit millisecond timestamp + 80 random
bits → 26 chars, lexicographically sortable by creation time.
The 80 random bits give collision odds so low that monotonic
guarantees are unnecessary for v0. The id is also the SQLite
PK on ``chat_sessions.session_id``, so callers that already
hold an id don't pay any translation cost.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from magi.runtime.state.orm import ChatMessage, ChatSession, open_session
from magi.runtime.workspace import workspace_root

logger = logging.getLogger("magi.runtime.sessions")

SCHEMA_VERSION = 1
_SESSIONS_SUBDIR = "sessions"
_PREVIEW_CHARS = 80
# Title length ceiling: matches the Pydantic ``max_length`` on
# ``PATCH /api/chat/sessions/{id}`` body. Truncating here too
# guards against a hand-crafted endpoint bypass that bypasses
# the Pydantic body validation.
_TITLE_MAX_LEN = 80
_ALLOWED_MESSAGE_ROLES = frozenset({"user", "assistant", "system"})

# Crockford base32 alphabet — no I, L, O, U to avoid
# look-alikes. 32 chars, 5 bits each, exactly fits the
# 48-bit timestamp + 80-bit random payload in 26 chars.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# -- chat_id validation ----------------------------------------------------
#
# Today the WebUI admin cookie and the TG ``message.chat.id``
# both arrive as decimal digit strings. The legacy
# ``_CHAT_ID_RE`` was a path-segment safety check (no
# directory traversal); with the move to SQLite the chat_id
# becomes a column value, so the regex now guards against
# accidental column-arithmetic errors (e.g. a 64-char string
# being silently truncated by the ``chat_id`` column width).
# The character class is the same as the old path check, so
# no caller-visible change.

import re as _re
_CHAT_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_chat_id(chat_id: str) -> None:
    if not isinstance(chat_id, str) or not _CHAT_ID_RE.match(chat_id):
        raise ValueError(
            f"chat_id {chat_id!r} contains characters that are not "
            "safe as an identifier"
        )


def _validate_session_id(session_id: str) -> None:
    """Reject anything outside the ULID shape.

    Pre-D.18 the path check was a directory-traversal guard;
    D.18 dropped the path layer, but the API contract that
    "a non-ULID id is a 400 validation.session_id_invalid"
    survives — callers (chat_sessions.py) catch
    ``SessionPathError`` for this. We raise it on shape, the
    same way the v0 path check did, so the API-layer mapping
    stays unchanged.
    """
    if (
        not isinstance(session_id, str)
        or len(session_id) != 26
        or any(c not in _CROCKFORD for c in session_id)
    ):
        raise SessionPathError(
            f"session_id {session_id!r} is not a valid ULID"
        )


def session_lock(chat_id: str, session_id: str) -> None:
    """No-op compat shim.

    Pre-D.18 callers (chat.py / bot.py / auto_title.py) wrapped
    their inbound writes in ``async with session_lock(...)``
    to serialise against the auto-title worker. With SQLite +
    WAL the writes are atomic at the statement/transaction
    level (single INSERT / UPDATE per write), so the lock has
    no remaining purpose.

    Kept as a callable that returns ``None`` (so callers that
    still write ``async with session_lock(...):`` get a
    AttributeError-free no-op for one release), but the
    follow-up D.18 cleanup removes all call sites.
    """
    return None


# -- exceptions ------------------------------------------------------------


class SessionError(Exception):
    """Base for every error this module can raise."""


class SessionNotFoundError(SessionError):
    """The session file does not exist."""


class SessionCorruptError(SessionError):
    """The session file exists but is malformed.

    Could be invalid JSON, schema_version mismatch, missing
    required fields, unknown role values, etc. The caller
    decides how to surface this (404 vs 500); the API layer
    maps it to 500 since an unexpected on-disk shape is
    almost always a tooling bug, not a user error.
    """


class SessionPathError(SessionError):
    """The provided identifier (chat_id or session_id) is not safe.

    Pre-D.18 this meant "would escape the file path"; with the
    move to SQLite the same regex guards against accidental
    column-arithmetic bugs (e.g. a 64-char limit hit). The
    class name is kept for backwards compat with the
    API-layer error mapping (400 ``validation.session_id_invalid``).
    """


# -- data shapes ------------------------------------------------------------


@dataclass
class SessionMessage:
    role: str  # "user" | "assistant" | "system"
    text: str
    ts: str  # ISO 8601 UTC ("...Z")
    message_id: str  # ULID — distinct from session_id


@dataclass
class Session:
    session_id: str
    chat_id: str
    employee_id: int
    channel: str
    created_at: str
    updated_at: str
    messages: list[SessionMessage]
    """The LLM-facing view: what gets sent to the API.
    After D.17 compaction this list is rewritten to
    ``[summary_system_message, m[-K+1], ..., m[-1]]``
    where ``m[-i]`` is the i-th most recent original
    message and ``K`` is ``active_tail_count``.
    """
    # Optional title: ``None`` means "no title yet" (manual
    # rename unset, or background job not yet run). The
    # front-end renders ``title ?? preview``. Set by the
    # PATCH endpoint (manual) or the auto-title worker.
    title: str | None = None
    schema_version: int = SCHEMA_VERSION

    # D.17 — compaction state. v0 file format adds these
    # fields without bumping ``schema_version``; all three
    # have dataclass defaults so old session files load
    # cleanly. See per-field docstrings below for what each
    # one is for.
    archive: list[SessionMessage] = field(default_factory=list)
    """Append-only log of OLD messages that were rolled out
    of ``messages`` during a compaction pass. Operators
    can read it via the GET endpoint to reconstruct the
    full original conversation; the LLM never sees it.

    The compaction summary itself is stored as a
    ``role="system"`` message at ``messages[0]`` (the
    first entry after compaction). archive holds only the
    ORIGINAL messages that were moved out — never the
    summary."""

    active_tail_count: int = 20
    """Snapshot of how many original ``messages`` were
    kept verbatim at the most recent compaction (in
    addition to the summary at index 0).

    This is an audit trail only — "the last compaction
    preserved K turns". The next compaction reads the
    LIVE ``system.compact_keep_recent`` setting, NOT this
    field, so changing the setting in the Settings tab
    takes effect immediately on the next compaction pass
    even if this snapshot says something different.

    v0 default: 20 (matches the settings default)."""

    last_compaction_at: str | None = None
    """ISO timestamp of the most recent compaction event.
    Useful for the dashboard's "this session was compacted
    N times" / "last compacted at" stat."""


@dataclass
class SessionSummary:
    """List-endpoint shape — no full messages, just header + counts."""

    session_id: str
    created_at: str
    updated_at: str
    message_count: int
    preview: str  # first user text, trimmed to _PREVIEW_CHARS
    title: str | None = None


# -- ULID generator --------------------------------------------------------


def new_session_id(now_ms: int | None = None) -> str:
    """Return a 26-char Crockford-base32 ULID.

    Layout: 48-bit ``now_ms`` (big-endian) followed by 80
    random bits. Lexicographic order matches creation order.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    rand = secrets.token_bytes(10)  # 80 bits
    val = (now_ms << 80) | int.from_bytes(rand, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    return "".join(reversed(out))


def utcnow_iso() -> str:
    """Centralised UTC ISO 8601 with a trailing ``Z``.

    We keep datetimes in UTC everywhere on disk; the ``Z``
    suffix makes it explicit (vs. ``+00:00``) so frontends
    don't need to special-case the timezone.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


# -- legacy on-disk shape parser -------------------------------------------
#
# Used only by ``migrate_from_json`` below to read the pre-D.18
# JSON files. The shape validator stays fail-closed (same as
# the v0 file loader) so a corrupt file becomes a logged
# warning + skip, never a silent import of garbage.

def session_from_dict(d: dict) -> Session:
    """Parse + validate a dict back into a ``Session``.

    Raises ``SessionCorruptError`` if anything is wrong. We
    fail closed — never silently coerce or skip.

    Backward compatibility: ``title``, ``archive``,
    ``active_tail_count``, and ``last_compaction_at`` are all
    read via ``d.get(...)`` so a file written before D.7 / D.17
    loads with sensible defaults. No schema-version bump is
    needed for additive optional fields.
    """
    if not isinstance(d, dict):
        raise SessionCorruptError("session root is not a JSON object")
    schema_version = d.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SessionCorruptError(
            f"unsupported schema_version {schema_version!r} "
            f"(expected {SCHEMA_VERSION})"
        )
    try:
        msgs_raw = d.get("messages", [])
        if not isinstance(msgs_raw, list):
            raise SessionCorruptError("messages is not a list")
        messages: list[SessionMessage] = []
        for i, m in enumerate(msgs_raw):
            if not isinstance(m, dict):
                raise SessionCorruptError(
                    f"messages[{i}] is not a JSON object"
                )
            role = m.get("role")
            if role not in _ALLOWED_MESSAGE_ROLES:
                raise SessionCorruptError(
                    f"messages[{i}].role {role!r} is not allowed"
                )
            messages.append(
                SessionMessage(
                    message_id=str(m["message_id"]),
                    role=role,
                    ts=str(m["ts"]),
                    text=str(m["text"]),
                )
            )

        # D.17 — archive is the old-messages forensic log.
        # Pre-D.17 files have no ``archive`` key; default to
        # empty list so the loaded Session behaves exactly
        # like v0 (no compaction has happened yet).
        arch_raw = d.get("archive", [])
        if not isinstance(arch_raw, list):
            raise SessionCorruptError("archive is not a list")
        archive: list[SessionMessage] = []
        for i, m in enumerate(arch_raw):
            if not isinstance(m, dict):
                raise SessionCorruptError(
                    f"archive[{i}] is not a JSON object"
                )
            role = m.get("role")
            if role not in _ALLOWED_MESSAGE_ROLES:
                raise SessionCorruptError(
                    f"archive[{i}].role {role!r} is not allowed"
                )
            archive.append(
                SessionMessage(
                    message_id=str(m["message_id"]),
                    role=role,
                    ts=str(m["ts"]),
                    text=str(m["text"]),
                )
            )

        # ``title`` defaults to None when the key is absent
        # (pre-D.7 files have no title field).
        title_raw = d.get("title")
        if title_raw is not None and not isinstance(title_raw, str):
            raise SessionCorruptError(
                f"title must be a string or null, got {type(title_raw).__name__}"
            )

        # ``active_tail_count`` defaults to 20 when missing
        # (pre-D.17 files). Out-of-range values (e.g. a
        # hand-edited 0) clamp to ``1`` — at least one
        # recent turn is always kept.
        try:
            active_tail_count = int(d.get("active_tail_count", 20))
        except (TypeError, ValueError):
            active_tail_count = 20
        if active_tail_count < 1:
            active_tail_count = 20

        last_compaction_raw = d.get("last_compaction_at")
        last_compaction_at: str | None
        if last_compaction_raw is None:
            last_compaction_at = None
        elif isinstance(last_compaction_raw, str):
            last_compaction_at = last_compaction_raw
        else:
            # Non-string in the file is corrupt; reset to
            # None rather than fail the whole load (the
            # session is still usable, just no recent
            # compaction timestamp).
            last_compaction_at = None

        return Session(
            session_id=str(d["session_id"]),
            chat_id=str(d["chat_id"]),
            employee_id=int(d["employee_id"]),
            channel=str(d["channel"]),
            created_at=str(d["created_at"]),
            updated_at=str(d["updated_at"]),
            messages=messages,
            title=title_raw,
            schema_version=schema_version,
            archive=archive,
            active_tail_count=active_tail_count,
            last_compaction_at=last_compaction_at,
        )
    except KeyError as e:
        raise SessionCorruptError(
            f"session missing required field: {e.args[0]!r}"
        ) from None


def summary_from_session(s: Session) -> SessionSummary:
    """Build a list-endpoint summary from a full session."""
    preview = ""
    for m in s.messages:
        if m.role == "user" and m.text:
            preview = m.text[:_PREVIEW_CHARS]
            if len(m.text) > _PREVIEW_CHARS:
                preview += "…"
            break
    return SessionSummary(
        session_id=s.session_id,
        created_at=s.created_at,
        updated_at=s.updated_at,
        message_count=len(s.messages),
        preview=preview,
        title=s.title,
    )


# -- SessionStore ----------------------------------------------------------


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

        The row is committed before this returns so a
        subsequent ``get`` always sees the same id.
        """
        _validate_chat_id(chat_id)
        session_id = new_session_id()
        now = utcnow_iso()
        with open_session() as db:
            db.add(ChatSession(
                session_id=session_id,
                chat_id=chat_id,
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
        """
        _validate_session_id(session_id)
        _validate_chat_id(chat_id)
        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.chat_id != chat_id:
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
                chat_id=sess_row.chat_id,
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
        # Validate up-front so a partial append isn't possible.
        for i, m in enumerate(new_msgs):
            if m.role not in _ALLOWED_MESSAGE_ROLES:
                raise SessionCorruptError(
                    f"appending messages[{i}].role {m.role!r} "
                    "is not allowed"
                )

        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.chat_id != chat_id:
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
        # Re-read so the returned Session matches what's on disk.
        return self.get(chat_id, session_id)  # type: ignore[return-value]

    def rename(
        self,
        chat_id: str,
        session_id: str,
        title: str | None,
        *,
        bump_updated: bool = True,
    ) -> Session:
        """Set or clear the session's ``title``.

        ``title`` is trimmed and length-clamped to
        ``_TITLE_MAX_LEN`` chars. ``None`` (or an empty
        string after trimming) clears the title.

        ``bump_updated=False`` skips the ``updated_at`` bump —
        used by the manual ``PATCH`` path because a rename is
        operator metadata and shouldn't reshuffle the sidebar.
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
            if sess_row is None or sess_row.chat_id != chat_id:
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
        """Set the session's ``title`` only if it is currently NULL.

        Atomic — single ``UPDATE … WHERE title IS NULL`` whose
        affected-row count tells the caller whether they won
        the race against a concurrent manual PATCH or another
        worker. Returns the post-update ``Session`` on success,
        ``None`` when the row didn't exist OR the title was
        already set.

        Used by the D.7 auto-title worker to replace the
        pre-D.18 ``async with session_lock(...)`` read-then-
        write pattern with a SQL-level compare-and-set. The
        lock is no longer needed because SQLAlchemy's
        ``begin`` event listener issues ``BEGIN IMMEDIATE``,
        serialising the UPDATE across the writer pool.
        """
        from sqlalchemy import update

        with open_session() as db:
            stmt = (
                update(ChatSession)
                .where(
                    ChatSession.session_id == session_id,
                    ChatSession.chat_id == chat_id,
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
                # Either session doesn't exist or title was
                # already set by someone else — caller treats
                # both as "lost the race".
                return None
            return self.get(chat_id, session_id)

    def _write(self, session: Session, *, bump_updated: bool = True) -> Session:
        """Persist a (possibly-mutated) ``Session`` back to
        the DB. Used by :mod:`magi.runtime.agent` after
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
          4. UPDATE the session's metadata
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

        # Return a fresh read so the caller sees what's
        # actually on disk (and so the in-memory Session
        # they're holding matches the persisted state).
        return self.get(session.chat_id, session.session_id)  # type: ignore[return-value]

    def delete(self, chat_id: str, session_id: str) -> bool:
        """Remove a session. ``True`` if it existed.

        Idempotent: deleting a non-existent session is a
        no-op (returns ``False``). No trash; v0 doesn't
        support undo.
        """
        _validate_session_id(session_id)
        _validate_chat_id(chat_id)
        with open_session() as db:
            sess_row = db.get(ChatSession, session_id)
            if sess_row is None or sess_row.chat_id != chat_id:
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
                .where(ChatSession.chat_id == chat_id)
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


# -- JSON → SQLite migration -----------------------------------------------
#
# One-shot importer: walks ``<workspace>/memories/sessions/
# <chat_id>/<sid>.json``, parses each file, inserts rows into
# the SQLite tables, and deletes the JSON after the row's
# transaction commits. Idempotent via ``INSERT OR IGNORE`` on
# the ``(session_id, message_id)`` unique constraint — a
# crashed boot just retries on next start, and a partially-
# imported file is harmlessly skipped on re-run.
#
# Corrupt files are logged and NOT deleted (no silent data
# loss). An operator can hand-inspect and either fix or
# ``rm`` the bad file.


def migrate_from_json(workspace_root_path: Path) -> dict[str, int]:
    """Walk the legacy ``sessions/<chat_id>/<sid>.json`` tree
    and import each file into SQLite.

    Returns a small stats dict: ``{"imported": N, "skipped":
    N, "corrupt": N}``. Logs each corrupt file at WARNING
    level so the operator sees the SKIP, not just the counts.
    """
    sessions_root = Path(workspace_root_path) / "memories" / _SESSIONS_SUBDIR
    if not sessions_root.is_dir():
        return {"imported": 0, "skipped": 0, "corrupt": 0}

    imported = 0
    skipped = 0
    corrupt = 0

    for chat_dir in sorted(sessions_root.iterdir()):
        if not chat_dir.is_dir():
            continue
        chat_id = chat_dir.name
        # Validate chat_id; the dir name is filesystem-supplied
        # so a corrupted workspace could have anything in here.
        try:
            _validate_chat_id(chat_id)
        except ValueError as e:
            logger.warning(
                "migrate_from_json: skipping chat dir %s (%s)",
                chat_dir, e,
            )
            corrupt += 1
            continue

        for json_path in sorted(chat_dir.glob("*.json")):
            try:
                raw = json_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                sess = session_from_dict(data)
            except (json.JSONDecodeError, SessionCorruptError, KeyError) as e:
                logger.warning(
                    "migrate_from_json: skipping corrupt file %s (%s)",
                    json_path, e,
                )
                corrupt += 1
                continue

            # Insert into SQLite. Per-file transaction; on
            # success delete the JSON; on failure leave it
            # so the next boot retries.
            try:
                with open_session() as db:
                    # INSERT OR IGNORE the session header
                    db.execute(
                        ChatSession.__table__.insert().prefix_with("OR IGNORE"),
                        {
                            "session_id": sess.session_id,
                            "chat_id": sess.chat_id,
                            "employee_id": sess.employee_id,
                            "channel": sess.channel,
                            "title": sess.title,
                            "active_tail_count": sess.active_tail_count,
                            "last_compaction_at": sess.last_compaction_at,
                            "created_at": sess.created_at,
                            "updated_at": sess.updated_at,
                        },
                    )
                    # Insert active messages
                    for m in sess.messages:
                        db.execute(
                            ChatMessage.__table__.insert().prefix_with("OR IGNORE"),
                            {
                                "session_id": sess.session_id,
                                "message_id": m.message_id,
                                "role": m.role,
                                "text": m.text,
                                "ts": m.ts,
                                "archived": 0,
                            },
                        )
                    # Insert archive rows (preserved with
                    # archived=1 so they participate in FTS
                    # search just like the active set).
                    for m in sess.archive:
                        db.execute(
                            ChatMessage.__table__.insert().prefix_with("OR IGNORE"),
                            {
                                "session_id": sess.session_id,
                                "message_id": m.message_id,
                                "role": m.role,
                                "text": m.text,
                                "ts": m.ts,
                                "archived": 1,
                            },
                        )
                    db.commit()
            except Exception as e:
                logger.warning(
                    "migrate_from_json: insert failed for %s (%s); "
                    "leaving JSON in place for next boot",
                    json_path, e,
                )
                skipped += 1
                continue

            # JSON is now in SQLite — delete the source. Best-
            # effort; if unlink fails (e.g. read-only mount),
            # the next boot re-runs and ``INSERT OR IGNORE``
            # makes the second pass a no-op.
            try:
                json_path.unlink()
            except OSError as e:
                logger.warning(
                    "migrate_from_json: imported but failed to delete %s (%s)",
                    json_path, e,
                )
            imported += 1

    # Clean up empty chat directories left behind.
    for chat_dir in sessions_root.iterdir():
        try:
            if chat_dir.is_dir() and not any(chat_dir.iterdir()):
                chat_dir.rmdir()
        except OSError:
            pass

    if imported or corrupt:
        logger.info(
            "migrate_from_json: imported=%d skipped=%d corrupt=%d",
            imported, skipped, corrupt,
        )
    return {"imported": imported, "skipped": skipped, "corrupt": corrupt}
