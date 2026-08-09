"""Connector boot — load enabled configs at runtime startup.

Called once from :mod:`magi.__main__::run` after the
private SQLite is initialised. Mirrors
:func:`magi.tools.registry.bootstrap_mcp_tools` in
shape: read the operator's config rows, construct +
connect every enabled connector, log the result.

Default vs persisted
--------------------
The full WebUI surface for managing connector configs
(schedule CRUD, per-connector auth storage, instance
toggle) is deferred — see ``magi/connectors/__init__.py``
for the design. The boot path reads the
``connector_configs`` table when it exists, falling back
to a single default calendar instance when it doesn't.

The fallback is what makes the sample
(``magi.connectors.samples.calendar``) work out of the
box on a fresh workspace — operators see one calendar
connector with a poll interval of 5 minutes, can edit
its settings via env vars, and that's it. The full DB
CRUD path arrives in a follow-up PR.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from magi.connectors.base import ConnectorConfig

logger = logging.getLogger("magi.connectors.boot")


# Default config — used when the DB table is absent
# (i.e. on a fresh workspace before the connector CRUD
# surface lands). Operators override via env vars:
# ``MAGI_CALENDAR_SOURCE`` (``osascript``/``ical``),
# ``MAGI_CALENDAR_PATH`` (for ical source),
# ``MAGI_CALENDAR_POLL`` (seconds, default 300).
def _default_configs() -> list[ConnectorConfig]:
    import os
    settings: dict[str, Any] = {}
    source = os.environ.get("MAGI_CALENDAR_SOURCE")
    if source:
        settings["source"] = source
    path = os.environ.get("MAGI_CALENDAR_PATH")
    if path:
        settings["path"] = path
    poll = os.environ.get("MAGI_CALENDAR_POLL")
    if poll:
        try:
            settings["poll_interval_seconds"] = float(poll)
        except ValueError:
            logger.warning(
                "MAGI_CALENDAR_POLL is not a number: %r", poll,
            )

    return [
        ConnectorConfig(
            name="calendar",
            instance_id="_default",
            enabled=True,
            settings=settings,
            auth=None,
        ),
    ]


def _build_configs() -> list[ConnectorConfig]:
    """Build the currently supported connector configuration.

    NewBus has no connector-configuration Book yet.  The old Bus-backed
    table was never exposed by the promised CRUD surface, so a clean
    cutover intentionally keeps only the documented environment/default
    configuration rather than retaining a hidden legacy read path.
    """
    return _default_configs()


def load_connectors_from_db() -> int:
    """Construct + connect every enabled config.

    Returns the number of connectors successfully
    connected. Errors are logged and skipped — a single
    bad row never blocks the rest of boot.

    Called from :mod:`magi.__main__::run` *before* uvicorn
    takes over, so no event loop is running yet —
    ``asyncio.run`` is the right call. If a future
    caller invokes this from inside an async context
    (FastAPI lifespan, etc.) they should call
    :func:`magi.connectors.registry.load_connectors`
    directly instead.
    """
    configs = _build_configs()
    if not configs:
        logger.info("connectors: no enabled configs")
        return 0

    from magi.connectors.registry import load_connectors as registry_load

    loaded = asyncio.run(registry_load(configs))
    logger.info(
        "connectors: %d enabled, %d loaded: %s",
        len(configs), len(loaded),
        [f"{c.name}/{getattr(c, '_instance_id', '?')}" for c in loaded],
    )
    return len(loaded)


__all__ = ["load_connectors_from_db"]
