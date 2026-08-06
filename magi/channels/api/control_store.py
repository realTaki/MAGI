"""PostgreSQL-backed state used exclusively by the singleton WebUI.

This module is the channel-side façade for the singleton WebUI's
PG-backed control-plane KV.  Reads and writes are forwarded to
:class:`magi.bus.jobs.services.magis.MagisService` so the channels → db
boundary stays one-way (channels must not import ``magi.db.*``
directly — see ``tests/architecture/test_import_boundaries.py``).

The ``enabled()`` flag is intentionally local: it's a pure env-var
check that doesn't touch the database, so wrapping it through the
bus would only add an indirection.
"""

from __future__ import annotations

import os

from magi.bus import get_bus


def _control():
    """Resolve the bus MagisService that owns the control-plane KV."""
    return get_bus().magis


def enabled() -> bool:
    return bool(os.environ.get("MAGIS_DATABASE_URL"))


def get(key: str) -> str | None:
    return _control().control_setting_get(key)


def set(key: str, value: str) -> None:
    _control().control_setting_set(key, value)


def delete(key: str) -> None:
    _control().control_setting_delete(key)


def list_prefix(prefix: str) -> dict[str, str]:
    return _control().control_setting_list_prefix(prefix)
