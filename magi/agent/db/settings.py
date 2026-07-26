"""Compatibility facade for the ORM-backed ``settings`` KV table.

The public ``state_get`` / ``state_set`` / ``state_delete`` names are kept
because many channel and agent modules use this small API. The table itself
is no longer accessed through a second raw ``sqlite3`` connection: every
operation goes through the shared SQLAlchemy engine and ``open_session()``.

This module is intentionally the only legacy KV facade. New code that needs
structured settings should use :class:`magi.agent.db.models_setting.Setting`
directly, while existing system-setting keys can continue using these
helpers until their callers are migrated.
"""

from __future__ import annotations

import os
from pathlib import Path

from magi.agent.db.engine import open_session
from magi.agent.db.models_setting import Setting


def _prepare_session(state_dir: str):
    """Return an ORM session bound to ``state_dir``.

    ``state_dir`` remains in the helper signature for compatibility with the
    pre-ORM KV API. ``open_session`` validates it against the process-wide
    engine and initialises the engine from it when the caller has not already
    configured ``MAGI_STATE_DIR``.
    """
    requested = str(Path(state_dir))
    if not os.environ.get("MAGI_STATE_DIR"):
        os.environ["MAGI_STATE_DIR"] = requested
    return open_session(requested)


def state_get(state_dir: str, key: str) -> str | None:
    """Return the setting value for ``key`` or ``None`` if unset."""
    with _prepare_session(state_dir) as db:
        setting = db.get(Setting, key)
        return setting.value if setting is not None else None


def state_set(state_dir: str, key: str, value: str) -> None:
    """Upsert ``key=value`` inside one ORM transaction."""
    with _prepare_session(state_dir) as db:
        setting = db.get(Setting, key)
        if setting is None:
            db.add(Setting(key=key, value=value))
        else:
            setting.value = value
        db.commit()


def state_delete(state_dir: str, key: str) -> None:
    """Delete ``key`` if present, inside one ORM transaction."""
    with _prepare_session(state_dir) as db:
        setting = db.get(Setting, key)
        if setting is not None:
            db.delete(setting)
        db.commit()


__all__ = ["state_get", "state_set", "state_delete"]
