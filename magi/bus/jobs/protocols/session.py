"""Public, persistence-free contracts for chat sessions."""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


SCHEMA_VERSION = 1
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SESSION_ID_RE = re.compile(r"^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}$")
_ALLOWED_MESSAGE_ROLES = frozenset({"user", "assistant", "system"})
_PREVIEW_CHARS = 80


class SessionError(Exception):
    """Base error for the session domain."""


class SessionNotFoundError(SessionError):
    """The requested session is absent or belongs to another owner."""


class SessionCorruptError(SessionError):
    """A caller attempted to persist an invalid session message."""


class SessionPathError(SessionError):
    """A session identifier is malformed."""


class ChannelMismatchError(SessionError):
    """A write came from a channel other than the session owner."""

    def __init__(self, *, session_id: str, session_channel: str, caller_channel: str) -> None:
        self.session_id = session_id
        self.session_channel = session_channel
        self.caller_channel = caller_channel
        super().__init__(
            f"session {session_id!r} is owned by channel {session_channel!r}; "
            f"caller is {caller_channel!r}"
        )


class SearchUnavailable(SessionError):
    """SQLite in this deployment was built without the FTS table."""


def new_session_id(now_ms: int | None = None) -> str:
    """Return a lexicographically sortable 26-character ULID."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    value = (now_ms << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise SessionPathError(f"session_id {session_id!r} is not a valid ULID")


def validate_uid(uid: int) -> None:
    if not isinstance(uid, int) or uid < 0:
        raise ValueError(f"uid {uid!r} is not a valid non-negative integer id")


@dataclass(slots=True)
class SessionMessage:
    role: str
    text: str
    ts: str
    message_id: str


@dataclass(slots=True)
class Session:
    session_id: str
    delivery_address: str
    uid: int
    channel: str
    created_at: str
    updated_at: str
    messages: list[SessionMessage]
    title: str | None = None
    schema_version: int = SCHEMA_VERSION
    archive: list[SessionMessage] = field(default_factory=list)
    active_tail_count: int = 20
    last_compaction_at: str | None = None


@dataclass(slots=True)
class SessionSummary:
    session_id: str
    created_at: str
    updated_at: str
    message_count: int
    preview: str
    title: str | None = None
    channel: str = "webui"


@dataclass(slots=True)
class SearchHit:
    session_id: str
    message_id: str
    role: str
    ts: str
    snippet: str
    score: float
    channel: str
    title: str | None = None
    delivery_address: str | None = None


__all__ = [
    "SCHEMA_VERSION", "SessionError", "SessionNotFoundError", "SessionCorruptError",
    "SessionPathError", "ChannelMismatchError", "SearchUnavailable", "new_session_id",
    "utcnow_iso", "validate_session_id", "validate_uid", "Session", "SessionMessage",
    "SessionSummary", "SearchHit",
]
