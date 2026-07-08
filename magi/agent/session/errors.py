"""Session module exception hierarchy.

Lives in its own module so :mod:`magi.agent.session.ids`
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
    """The provided identifier (chat_id or session_id) is not safe.

    Pre-D.18 this meant "would escape the file path"; with the
    move to SQLite the same regex guards against accidental
    column-arithmetic bugs (e.g. a 64-char limit hit). The
    class name is kept for backwards compat with the
    API-layer error mapping (400 ``validation.session_id_invalid``).
    """
