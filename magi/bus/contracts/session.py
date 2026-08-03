"""Session DTOs (pure data, no I/O, no ORM).

The canonical shapes for chat sessions moving through the bus. The bus
services return these — never SQLAlchemy ``ChatSession`` / ``ChatMessage``
rows. Callers (agent / tools / channels) see only DTOs.

Source of truth: ``magi/agent/memory/session/models.py`` (provisional —
will move fully into bus at end of migration). The re-exports here are
the public contract; the implementation file is the legacy location.

The error hierarchy is also part of the public bus contract: a caller
catching ``SessionNotFoundError`` must not need to import the legacy
package to do so.
"""

from __future__ import annotations

# Re-exports during the entity-by-entity migration. These imports live
# under the bus but the underlying modules still sit at
# ``magi.agent.memory.session.*`` until that whole package is deleted.
# At that point the implementations move to
# ``magi.bus.services.session`` and the contracts continue to re-export
# the same public names.
from magi.agent.memory.session.errors import (
    ChannelMismatchError,
    SessionCorruptError,
    SessionError,
    SessionNotFoundError,
    SessionPathError,
)
from magi.agent.memory.session.ids import (
    new_session_id,
    utcnow_iso,
)
from magi.agent.memory.session.models import (
    SCHEMA_VERSION,
    Session,
    SessionMessage,
    SessionSummary,
    session_from_dict,
    summary_from_session,
)
from magi.agent.memory.session.search import SearchHit


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
    # models
    "SCHEMA_VERSION",
    "Session",
    "SessionMessage",
    "SessionSummary",
    "SearchHit",
    # legacy parsers (kept for the JSON importer; not used by the bus
    # runtime path)
    "session_from_dict",
    "summary_from_session",
]
