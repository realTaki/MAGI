"""PostgreSQL-backed state used exclusively by the singleton WebUI.

This module is the channel-side façade for the singleton WebUI's
PG-backed control-plane KV. Reads and writes are forwarded through the bus
facade so the channel layer does not open persistence directly.

The ``enabled()`` flag is intentionally local: it's a pure env-var
check that doesn't touch the database, so wrapping it through the
bus would only add an indirection.
"""

from __future__ import annotations

import os

from magi.bus import Bus


def _control(bus: Bus):
    """Return the explicit settings Book for this control application."""
    return bus.settings_book


def enabled() -> bool:
    return bool(os.environ.get("MAGIS_DATABASE_URL"))


def get(bus: Bus, key: str) -> str | None:
    return _control(bus).get(key=key)


def set(bus: Bus, key: str, value: str) -> None:
    _control(bus).set(key=key, value=value)


def delete(bus: Bus, key: str) -> None:
    _control(bus).delete(key=key)


def list_prefix(bus: Bus, prefix: str) -> dict[str, str]:
    return {
        setting.key: setting.value
        for setting in _control(bus).list_all()
        if setting.key.startswith(prefix)
    }
