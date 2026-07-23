"""Session module exception hierarchy.

Lives in its own module so :mod:`magi.agent.memory.session.ids`
can raise ``SessionPathError`` for shape validation without
a circular import (ids.py is imported by models.py for the
Crockford alphabet; models.py is imported by ids.py for
the error class — splitting errors out breaks the cycle
cleanly at zero runtime cost).
"""

from __future__ import annotations


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
    """The provided identifier (tgid or session_id) is not safe.

    Pre-D.18 this meant "would escape the file path"; with the
    move to SQLite the same regex guards against accidental
    column-arithmetic bugs (e.g. a 64-char limit hit). The
    class name is kept for backwards compat with the
    API-layer error mapping (400 ``validation.session_id_invalid``).
    """


class ChannelMismatchError(SessionError):
    """The caller's channel does not own the session.

    Sessions are pinned to a single channel at creation
    time (TG / webui / scheduled). Reads (list, get) are
    cross-channel by design — the same employee may browse
    their TG history from the WebUI console — but **writes**
    (append_messages, the inbound that triggers
    handle_message) must come from the owner channel. The
    cross-channel race ("two LLM loops writing the same
    session simultaneously") is the failure mode this guard
    closes.

    Surfaces to the API as 403 ``chat.session_channel_mismatch``
    with the session's owning channel in the detail string
    so the UI can render "this session was started on TG,
    continue the conversation there".

    A session whose ``channel`` column is empty (legacy
    pre-D.18 row) does NOT trigger this error — the
    ownership is unknown, so the writer wins.
    """

    def __init__(
        self,
        *,
        session_id: str,
        session_channel: str,
        caller_channel: str,
    ) -> None:
        self.session_id = session_id
        self.session_channel = session_channel
        self.caller_channel = caller_channel
        super().__init__(
            f"session {session_id!r} is owned by channel "
            f"{session_channel!r}; caller is {caller_channel!r}"
        )
