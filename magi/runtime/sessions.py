"""Chat session storage — file-backed conversation history.

A session is one thread of messages between an operator (or
eventually an EVE) and the system LLM, persisted as a JSON
file under the operator's workspace. Two operators share no
state — the file path embeds the ``chat_id`` so the directory
layout itself enforces per-user isolation.

Layout::

    <workspace>/memories/sessions/<chat_id>/<session_id>.json

Each session file holds the entire transcript + a small
header. The JSON is fully rewritten on every append because
sessions are short (a single-operator chat, typically
tens-to-hundreds of messages); a fancy append-only format
(NDJSON / WAL) would buy nothing for v0.

Atomicity
---------

The single-writer invariant per session (one browser tab per
operator) holds in v0. Concurrent writers on the same session
file would race on ``os.replace`` and clobber each other —
acceptable, the assumption is documented and tested as the
single-threaded case.

Cross-process atomicity goes through ``tempfile.mkstemp`` +
``os.fsync`` + ``os.replace``: the target file is either the
old contents or the new, never a half-written intermediate.

ULID session id
---------------

We do not depend on the ``python-ulid`` package. Crockford base32
of a 48-bit millisecond timestamp followed by 80 random bits →
26 chars, lexicographically sortable by creation time. The 80
random bits give collision odds so low that monotonic
guarantees are unnecessary for v0.

Schema versioning
-----------------

``schema_version: 1`` is written into every JSON. A future v2
schema would add a ``_migrate_v1_to_v2`` helper, called on
every ``get``/``append`` until all files have moved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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

# A chat_id becomes a path segment; restrict to the same
# character class Telegram chat_ids already obey (digits)
# plus a few safe extras. The cookie stores it as a string
# of digits today, but the path helper is permissive so a
# future ``MAGI_USER_<handle>`` style doesn't break.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# -- per-session locks ------------------------------------------------------
#
# Cross-writer serialisation within a single event loop.
# The lock guarantees that a session file's state (messages
# list vs. title) is observed coherently by exactly one writer
# at a time. Cross-process safety still relies on the file
# atomic-write (``tempfile + os.replace``); this is a
# single-process defence-in-depth measure for the title-job /
# chat-send race D.7 introduced.
#
# Entries are created on first request and live for the
# process lifetime. The cache grows unboundedly but v0's
# per-operator session count is bounded (tens-to-hundreds);
# memory cost is one ``asyncio.Lock`` per (chat_id, session_id).
#
# ``_session_locks_meta_lock`` protects dict mutation (the
# ``.setdefault`` below) from concurrent first-callers
# across the asyncio / threading boundaries. Once the entry
# exists, lookup + ``acquire()`` is already thread-safe by
# virtue of the ``dict`` being atomic under the GIL.
_session_locks: dict[tuple[str, str], "asyncio.Lock"] = {}
_session_locks_meta_lock = threading.Lock()


def session_lock(chat_id: str, session_id: str) -> "asyncio.Lock":
    """Return the per-(chat_id, session_id) ``asyncio.Lock``,
    creating it lazily on first call.

    Callers acquire the returned lock around
    ``SessionStore.{append_messages, rename, get}`` for
    any session they expect to mutate under the D.7 title
    race window. The lock is intentionally not held across
    LLM calls (those are slow and would serialise all
    titles + sends through one mutex — overkill for v0).
    """
    key = (chat_id, session_id)
    lock = _session_locks.get(key)
    if lock is not None:
        return lock
    with _session_locks_meta_lock:
        lock = _session_locks.get(key)
        if lock is not None:
            return lock
        lock = asyncio.Lock()
        _session_locks[key] = lock
    return lock


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
    """The provided chat_id is not safe to use as a path segment.

    The API layer maps this to 400 validation.session_id_invalid.
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
    # cleanly. ``archive`` is an append-only log of the OLD
    # messages that were rolled out of ``messages`` during
    # a compaction pass — operators can read it via the
    # GET endpoint to reconstruct the full original
    # conversation, but the LLM never sees it.
    #
    # The compaction summary itself is stored as a
    # ``role="system"`` message at ``messages[0]`` (the
    # first entry after compaction). archive holds only the
    # ORIGINAL messages that were moved out — never the
    # summary.
    archive: list[SessionMessage] = field(default_factory=list)
    active_tail_count: int = 20
    last_compaction_at: str | None = None


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


# -- path helpers ----------------------------------------------------------


def session_dir(workspace_root_path: Path, chat_id: str) -> Path:
    """The directory holding this chat's session JSON files."""
    if not _CHAT_ID_RE.fullmatch(chat_id):
        raise SessionPathError(
            f"chat_id {chat_id!r} contains characters that are not "
            "safe as a path segment"
        )
    return (
        Path(workspace_root_path)
        / "memories"
        / _SESSIONS_SUBDIR
        / chat_id
    )


def session_path(workspace_root_path: Path, chat_id: str, session_id: str) -> Path:
    """The single JSON file for this chat + session."""
    # We deliberately don't validate session_id the same way
    # as chat_id. The ULID generator's output is guaranteed
    # safe (Crockford base32, no `/`), but a hand-crafted id
    # from the URL could include anything. Reject anything
    # outside the ULID alphabet up front.
    if (
        len(session_id) != 26
        or any(c not in _CROCKFORD for c in session_id)
    ):
        raise SessionPathError(
            f"session_id {session_id!r} is not a valid ULID"
        )
    return session_dir(workspace_root_path, chat_id) / f"{session_id}.json"


# -- atomic write ----------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write a JSON document atomically.

    The combination of ``mkstemp`` (creates the temp file
    in the *target* directory so ``os.replace`` is atomic
    on the same filesystem) + ``flush`` + ``fsync`` +
    ``os.replace`` is the POSIX-safe pattern. Half-written
    files can never appear as ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp.", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the temp file. Use missing_ok
        # so a successful ``os.replace`` above (which moves
        # the tmp out) is fine.
        Path(tmp_path).unlink(missing_ok=True)
        raise


# -- serialization --------------------------------------------------------


def session_to_dict(s: Session) -> dict:
    return {
        "schema_version": s.schema_version,
        "session_id": s.session_id,
        "chat_id": s.chat_id,
        "employee_id": s.employee_id,
        "channel": s.channel,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        # ``title`` is always emitted (``None`` → JSON null) so
        # the on-disk shape is unambiguous for debug tools.
        # ``session_from_dict`` accepts absence for backward
        # compatibility with files written before D.7.
        "title": s.title,
        "messages": [
            {
                "message_id": m.message_id,
                "role": m.role,
                "ts": m.ts,
                "text": m.text,
            }
            for m in s.messages
        ],
        # D.17 — archive + compaction metadata. All three
        # default to safe values for files written before
        # D.17; ``session_from_dict`` uses ``.get(...)`` so
        # missing keys load as defaults.
        "archive": [
            {
                "message_id": m.message_id,
                "role": m.role,
                "ts": m.ts,
                "text": m.text,
            }
            for m in s.archive
        ],
        "active_tail_count": s.active_tail_count,
        "last_compaction_at": s.last_compaction_at,
    }


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
    """File-backed session storage for one workspace.

    Stateless — safe to instantiate per-request. The
    workspace root is computed once via
    :func:`magi.runtime.workspace.workspace_root`.
    """

    state_dir: str | os.PathLike[str]
    _workspace: Path = field(init=False)

    def __post_init__(self) -> None:
        self._workspace = Path(workspace_root(self.state_dir))

    # -- public -----------------------------------------------------------

    def create(
        self,
        chat_id: str,
        *,
        employee_id: int,
        channel: str = "webui",
    ) -> Session:
        """Create a new empty session and persist it.

        Mints a fresh ULID; the on-disk file is created
        before this returns so a subsequent ``get`` always
        sees the same id.
        """
        session_id = new_session_id()
        now = utcnow_iso()
        session = Session(
            session_id=session_id,
            chat_id=chat_id,
            employee_id=employee_id,
            channel=channel,
            created_at=now,
            updated_at=now,
            messages=[],
        )
        path = session_path(self._workspace, chat_id, session_id)
        _atomic_write_json(path, session_to_dict(session))
        logger.info(
            "session created",
            extra={
                "session_id": session_id,
                "chat_id": chat_id,
                "employee_id": employee_id,
            },
        )
        return session

    def get(self, chat_id: str, session_id: str) -> Session | None:
        """Read a session by id. Returns ``None`` if missing.

        A malformed JSON file raises ``SessionCorruptError``
        rather than returning ``None`` so the API layer
        can distinguish "no such session" from "broken
        session" and respond 404 vs 500 respectively.
        """
        path = session_path(self._workspace, chat_id, session_id)
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SessionCorruptError(
                f"session file {path} is not valid JSON: {e}"
            ) from e
        return session_from_dict(data)

    def append_messages(
        self,
        chat_id: str,
        session_id: str,
        msgs: Iterable[SessionMessage],
        *,
        bump_updated: bool = True,
    ) -> Session:
        """Append one or more messages and rewrite the file.

        The full file is rewritten (not appended) because
        sessions are small. ``bump_updated=False`` is unused
        for v0 but kept in the signature so future
        callers (manual edits, schema-migration wipes)
        can re-write without touching ``updated_at``.
        """
        path = session_path(self._workspace, chat_id, session_id)
        if not path.is_file():
            raise SessionNotFoundError(
                f"session {session_id!r} for chat_id {chat_id!r} "
                "does not exist"
            )
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            session = session_from_dict(data)
        except json.JSONDecodeError as e:
            raise SessionCorruptError(
                f"session file {path} is not valid JSON: {e}"
            ) from e

        # Validate each incoming message before mutating
        # in-memory state. Keeps partial-write impossible
        # (the file is only touched after the validation
        # loop completes).
        new_msgs = list(msgs)
        for i, m in enumerate(new_msgs):
            if m.role not in _ALLOWED_MESSAGE_ROLES:
                raise SessionCorruptError(
                    f"appending messages[{i}].role {m.role!r} "
                    "is not allowed"
                )

        session.messages.extend(new_msgs)
        if bump_updated:
            session.updated_at = utcnow_iso()
        _atomic_write_json(path, session_to_dict(session))
        return session

    def rename(
        self,
        chat_id: str,
        session_id: str,
        title: str | None,
        *,
        bump_updated: bool = True,
    ) -> Session:
        """Set or clear the session's ``title``.

        Read-modify-rewrite via the same atomic-write path as
        :meth:`append_messages`. ``title`` is trimmed and length-
        clamped to ``_TITLE_MAX_LEN`` chars. ``None`` (or an
        empty string after trimming) clears the title.

        ``bump_updated=False`` skips the ``updated_at`` bump —
        used by the manual ``PATCH`` path because a rename is
        operator metadata and shouldn't reshuffle the sidebar.
        The background auto-title job passes the default
        (``bump_updated=True``) so a freshly-titled session
        floats to the top.

        Raises
        ------
        SessionNotFoundError
            No session file at the canonical path.
        SessionCorruptError
            The file exists but is malformed.
        SessionPathError
            ``session_id`` is not a valid ULID.
        """
        path = session_path(self._workspace, chat_id, session_id)
        if not path.is_file():
            raise SessionNotFoundError(
                f"session {session_id!r} for chat_id {chat_id!r} "
                "does not exist"
            )
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            session = session_from_dict(data)
        except json.JSONDecodeError as e:
            raise SessionCorruptError(
                f"session file {path} is not valid JSON: {e}"
            ) from e

        # Normalise: trim, length-clamp, treat empty as clear.
        if title is None:
            new_title: str | None = None
        else:
            stripped = title.strip()
            if not stripped:
                new_title = None
            else:
                new_title = stripped[:_TITLE_MAX_LEN]

        session.title = new_title
        if bump_updated:
            session.updated_at = utcnow_iso()
        _atomic_write_json(path, session_to_dict(session))
        logger.info(
            "session renamed",
            extra={
                "session_id": session_id,
                "chat_id": chat_id,
                "title_set": new_title is not None,
            },
        )
        return session

    def _write(self, session: Session, *, bump_updated: bool = True) -> Session:
        """Persist a (possibly-mutated) ``Session`` back to
        its file. Used by :mod:`magi.runtime.agent` after
        auto-compaction rewrites ``session.messages`` and
        appends to ``session.archive``.

        ``bump_updated=True`` (default) refreshes
        ``updated_at`` so the session floats to the top of
        the sidebar — the right default for "new content
        just landed". Set to ``False`` for ops that touch
        fields that aren't visible in the sidebar (e.g.
        ``last_compaction_at``) so the order doesn't shift
        just because of a background housekeeping event.

        Atomic: writes via ``_atomic_write_json`` so a crash
        mid-write leaves the previous version intact. The
        file's perms (``0o600``) are inherited from
        ``mkstemp`` in the atomic helper.
        """
        if bump_updated:
            session.updated_at = utcnow_iso()
        path = session_path(self._workspace, session.chat_id, session.session_id)
        _atomic_write_json(path, session_to_dict(session))
        return session

    def delete(self, chat_id: str, session_id: str) -> bool:
        """Remove a session file. ``True`` if it existed.

        Idempotent: deleting a non-existent session is a
        no-op (returns ``False``). No trash; v0 doesn't
        support undo.
        """
        path = session_path(self._workspace, chat_id, session_id)
        if not path.is_file():
            return False
        path.unlink()
        logger.info(
            "session deleted",
            extra={
                "session_id": session_id,
                "chat_id": chat_id,
            },
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

        Sorts by ``updated_at`` descending. Loading every
        session file per call is fine for v0 — a single
        operator's session count is small (tens-to-hundreds).
        A future scale-up could maintain a manifest file
        next to the directory.
        """
        directory = session_dir(self._workspace, chat_id)
        if not directory.is_dir():
            return [], 0

        # Read every session, build summary, drop the ones
        # that failed to parse (logged but not fatal — a
        # single corrupt file should not deny the operator
        # their other sessions).
        summaries: list[SessionSummary] = []
        corrupted = 0
        for child in directory.glob("*.json"):
            try:
                raw = child.read_text(encoding="utf-8")
                data = json.loads(raw)
                sess = session_from_dict(data)
            except (SessionCorruptError, json.JSONDecodeError) as e:
                logger.warning(
                    "skipping corrupt session file %s: %s", child, e,
                )
                corrupted += 1
                continue
            summaries.append(summary_from_session(sess))

        # Latest first by updated_at; ULIDs already encode
        # creation order, but updated_at wins because a
        # long session that's been idle should still rank
        # above a new empty one.
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        total = len(summaries)
        page = summaries[offset : offset + limit]
        if corrupted:
            logger.info(
                "list_summaries: skipped %d corrupt file(s) in %s",
                corrupted, directory,
            )
        return page, total
