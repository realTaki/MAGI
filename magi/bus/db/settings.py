
"""Compatibility facade for the ORM-backed ``settings`` KV table.

The public ``state_get`` / ``state_set`` / ``state_delete`` names are kept
because many channel and agent modules use this small API. The table itself
is no longer accessed through a second raw ``sqlite3`` connection: every
operation goes through the shared SQLAlchemy engine and ``open_session()``.

This module is intentionally the only legacy KV facade. New code that needs
structured settings should use :class:`magi.bus.models.local.setting.Setting`
directly, while existing system-setting keys can continue using these
helpers until their callers are migrated.
"""

from __future__ import annotations

from pathlib import Path

from magi.bus.db.engine import open_session
from magi.bus.models.local.setting import Setting


def _prepare_session(state_dir: str):
    """Return an ORM session bound to ``state_dir``."""
    return open_session(str(Path(state_dir)))


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


def settings_get_for(state_dir: str, key: str) -> str | None:
    """Composition-Root-aware variant of :func:`state_get`.

    Accepts an explicit ``state_dir`` rather than relying on
    the runtime state directory.  Phase 1 callers fall back to state_get();
    Phase 3's Local Profile may pass a per-MAGIS state directory.
    """
    with _prepare_session(state_dir) as db:
        setting = db.get(Setting, key)
        return setting.value if setting is not None else None


def settings_set_for(state_dir: str, key: str, value: str) -> None:
    """Composition-Root-aware variant of :func:`state_set`."""
    with _prepare_session(state_dir) as db:
        setting = db.get(Setting, key)
        if setting is None:
            db.add(Setting(key=key, value=value))
        else:
            setting.value = value
        db.commit()


def settings_delete_for(state_dir: str, key: str) -> bool:
    """Composition-Root-aware variant of :func:`state_delete`.  Returns whether a row was removed."""
    with _prepare_session(state_dir) as db:
        setting = db.get(Setting, key)
        if setting is None:
            db.commit()
            return False
        db.delete(setting)
        db.commit()
        return True


__all__ = [
    "state_get",
    "state_set",
    "state_delete",
    "settings_get_for",
    "settings_set_for",
    "settings_delete_for",
]
