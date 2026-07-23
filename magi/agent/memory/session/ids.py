"""Session id generation + validation + timestamp helpers.

Layer: low-level primitives. Used by the data-shape
module (``models.py``) for validator references and the
store (``store.py``) for inserting fresh session ids.
Has no dependency on SQLAlchemy — safe to import from
workers (``auto_title``) and tests that just want
ULIDs / current-time ISO strings.
"""

from __future__ import annotations

import re as _re
import secrets
import time
from datetime import datetime, timezone

from magi.agent.memory.session.errors import SessionPathError


# Crockford base32 alphabet — no I, L, O, U to avoid
# look-alikes. 32 chars, 5 bits each, exactly fits the
# 48-bit timestamp + 80-bit random payload in 26 chars.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Today the WebUI admin cookie and the TG ``message.chat.id``
# both arrive as decimal digit strings. The legacy
# ``_CHAT_ID_RE`` was a path-segment safety check (no
# directory traversal); with the move to SQLite the tgid
# becomes a column value, so the regex now guards against
# accidental column-arithmetic errors (e.g. a 64-char string
# being silently truncated by the ``tgid`` column width).
# The character class is the same as the old path check, so
# no caller-visible change.
_CHAT_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# D.23 — session identity is now ``uid: int`` (an
# int coerced to its decimal string form on the wire), not
# the channel-shaped ``tgid``. ``_EMPLOYEE_ID_RE`` is the
# validation regex for that new key; ``_CHAT_ID_RE`` is kept
# for the D.18 JSON importer (``migration.py``) which still
# uses the column's ``tgid`` value to build the legacy
# ``<workspace>/memories/sessions/<tgid>/<sid>.json`` path.
_EMPLOYEE_ID_RE = _re.compile(r"^[0-9]{1,19}$")


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


# ``utcnow_naive`` (the canonical "replacement for the
# deprecated ``datetime.utcnow()``") now lives in
# :mod:`magi.agent.db.base` so the ORM model files can
# import it without going through ``magi.agent.memory.__init__``
# (which transitively imports the contact tools, which
# import from ``magi.agent.db`` — the resulting cycle would
# deadlock module init). The companion ``utcnow_iso`` here
# stays put because it's a session-package helper that only
# touches the standard library + a path.
__all__ = ["new_session_id", "utcnow_iso", "_validate_session_id",
           "_validate_chat_id", "_validate_employee_id", "session_lock"]


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


def _validate_chat_id(tgid: str) -> None:
    if not isinstance(tgid, str) or not _CHAT_ID_RE.match(tgid):
        raise ValueError(
            f"tgid {tgid!r} contains characters that are not "
            "safe as an identifier"
        )


def _validate_employee_id(uid) -> None:
    """D.23 — the session key is now ``uid`` (int).

    Accepted forms (all coerced to the same ``int``):

      - ``int`` (preferred) — caller's ORM/cookie path.
      - ``str`` of decimal digits — when crossing a JSON
        boundary or a cookie that stores the id as a
        stringified int (the cookie is currently a
        ``telegram_id`` string, but a future "switch to
        uid cookie" path will pass it through
        here as a string).

    The ``int`` form is checked first so a hand-crafted
    caller that mistakenly passes a non-numeric string
    raises with a clearer error message than a column-
    overflow later.
    """
    if isinstance(uid, int):
        if uid < 0:
            raise ValueError(
                f"uid {uid!r} must be non-negative"
            )
        return
    if (
        not isinstance(uid, str)
        or not _EMPLOYEE_ID_RE.match(uid)
    ):
        raise ValueError(
            f"uid {uid!r} is not a valid integer id"
        )


def session_lock(tgid: str, session_id: str) -> None:
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
