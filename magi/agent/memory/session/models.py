"""Session data shapes + legacy JSON shape parser.

Pure data — no I/O, no SQLAlchemy. The parser
(``session_from_dict``) is here because it's just
deserialising the v0 on-disk shape into the dataclasses;
the migration that walks the directory and calls it lives
in :mod:`magi.agent.session.migration`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from magi.agent.session.errors import SessionCorruptError
from magi.agent.session.ids import _CROCKFORD


SCHEMA_VERSION = 1
_ALLOWED_MESSAGE_ROLES = frozenset({"user", "assistant", "system"})
_PREVIEW_CHARS = 80


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
