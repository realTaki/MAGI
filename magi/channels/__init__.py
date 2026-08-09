"""Channel adapters for MAGI.

A channel receives inbound messages from a user surface (Telegram chat,
WebUI console, future email / calendar) and sends outbound messages back.
Both ADAM and EVA mount one or more channels. They publish messages to the
private ``Bus``; the MAGI-owned agent worker consumes them sequentially.

``channels/base.py`` defines the abstract ``Channel`` interface
(receive / send / identify_sender). ``channels/worker_base.py`` defines
the ``ChannelWorker`` ABC for per-channel Worker implementations.

Concrete adapters:

- ``channels.telegram`` — EVA side, python-telegram-bot v21+ (C3).
- ``channels.webui``    — ADAM side, FastAPI + HTMX + WS (C1 for CRUD, C7 for chat console).

The :class:`Channel` enum is owned by the Bus task domain and re-exported
here as the common channel vocabulary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from magi.channels.worker_base import ChannelWorker
from magi.bus.library.local.tasksBook import Channel

if TYPE_CHECKING:
    from magi.bus import Bus

# Adapter bridge: worker ownership is centralized in ``magi.startup``;
# adapters may still need the process BUS selected by the composition root.
_current_bus: "Bus | None" = None


def set_current_bus(bus: "Bus") -> None:
    global _current_bus
    _current_bus = bus


def get_current_bus() -> "Bus | None":
    return _current_bus


__all__ = [
    "base",
    "worker_base",
    "Channel",
    "ChannelWorker",
    "set_current_bus",
    "get_current_bus",
]
