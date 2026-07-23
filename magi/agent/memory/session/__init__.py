"""Chat session management — store, lifecycle, auto-titler.

D.18: sessions moved from per-session JSON files under
``<workspace>/memories/sessions/<tgid>/<sid>.json`` to two
SQLAlchemy tables in ``magi.db`` (``chat_sessions`` + ``chat_messages``).
The ``SessionStore`` class kept the same public method
signatures so the ~30 callers (chat.py / bot.py / agent.py /
auto_title.py / chat_sessions.py) didn\'t need to change.

Singular ``session`` (not ``sessions``) because this package
is the *manager* of the chat-session concept — the data
model, the store, the auto-title worker, the migration
importer. The actual bulk storage lives one layer down
in :mod:`magi.agent.db` (the SQLAlchemy tables and
engine).

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

  - ACID transactions: compaction\'s archive + summary insert
    commit atomically (no more partial files).
  - FTS5 with trigram tokenizer for substring search across
    the whole history (including the D.17 archive tail).
  - Single-process reader concurrency via WAL; writer
    contention handled by ``busy_timeout=5000`` (set via the
    SQLAlchemy ``connect`` event listener in ``orm.py``).

Per-chat isolation that used to come "for free" from the
directory layout is now an explicit ``WHERE tgid = :caller``
clause in every read/write path. The chat_sessions routes
that read by ``tgid`` from the admin cookie enforce this
consistently.

Migration
---------

``migrate_from_json(workspace_root)`` (in :mod:`.migration`)
reads any leftover ``*.json`` files under
``<workspace>/memories/sessions/``, inserts them as rows,
and deletes the JSON after each row\'s transaction commits.
``INSERT OR IGNORE`` on the ``(session_id, message_id)``
unique constraint makes re-runs idempotent — a crashed boot
just retries on next start. Corrupt files are logged and
NOT deleted (no silent data loss); they can be hand-inspected
and either fixed or removed by the operator.

ULID session id
---------------

Crockford base32 of a 48-bit millisecond timestamp + 80 random
bits → 26 chars, lexicographically sortable by creation time.
The 80 random bits give collision odds so low that monotonic
guarantees are unnecessary for v0. The id is also the SQLite
PK on ``chat_sessions.session_id``, so callers that already
hold an id don\'t pay any translation cost.

Layout
------

  - :mod:`.errors`     — SessionError hierarchy
  - :mod:`.ids`        — ULID generator + validators + timestamps
  - :mod:`.models`     — dataclasses + legacy JSON parser
  - :mod:`.store`      — SessionStore (the SQLite-backed CRUD)
  - :mod:`.migration`  — JSON → SQLite importer (D.18)
  - :mod:`.auto_title`— background worker that titles sessions
"""

from __future__ import annotations

import logging

# Re-export the public surface. Module layout above; the
# names below are what the ~30 external callers import.
from magi.agent.memory.session.errors import (
    ChannelMismatchError,
    SessionCorruptError,
    SessionError,
    SessionNotFoundError,
    SessionPathError,
)
from magi.agent.memory.session.ids import (
    _validate_chat_id,
    _validate_uid,
    _validate_session_id,
    new_session_id,
    session_lock,
    utcnow_iso,
)
from magi.agent.memory.session.migration import migrate_from_json
from magi.agent.memory.session.models import (
    SCHEMA_VERSION,
    Session,
    SessionMessage,
    SessionSummary,
    session_from_dict,
    summary_from_session,
)
from magi.agent.memory.session.store import SessionStore


logger = logging.getLogger("magi.agent.memory.session")


__all__ = [
    # errors
    "SessionError",
    "SessionNotFoundError",
    "SessionCorruptError",
    "SessionPathError",
    "ChannelMismatchError",
    # ids
    "new_session_id",
    "utcnow_iso",
    "session_lock",
    # models
    "SCHEMA_VERSION",
    "Session",
    "SessionMessage",
    "SessionSummary",
    "session_from_dict",
    "summary_from_session",
    # store + migration
    "SessionStore",
    "migrate_from_json",
    # internal helpers exposed for sibling modules
    "_validate_chat_id",
    "_validate_uid",
    "_validate_session_id",
]
