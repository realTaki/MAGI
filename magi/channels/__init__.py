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

__all__ = [
    "base",
    "worker_base",
    "Channel",
    "ChannelWorker",
]
