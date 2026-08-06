"""Channel adapters for MAGI.

A channel receives inbound messages from a user surface (Telegram chat,
WebUI console, future email / calendar) and sends outbound messages back.
Both ADAM and EVA mount one or more channels. They publish messages to the
private ``magi.bus``; the MAGI-owned agent worker consumes them sequentially.

``channels/base.py`` defines the abstract ``Channel`` interface
(receive / send / identify_sender). Concrete adapters:

- ``channels.telegram`` — EVA side, python-telegram-bot v21+ (C3).
- ``channels.webui``    — ADAM side, FastAPI + HTMX + WS (C1 for CRUD, C7 for chat console).

The :class:`Channel` enum is owned by the bus (see
:mod:`magi.bus.jobs.protocols.channels`); this module re-exports it for
back-compat with code that still does ``from magi.channels import Channel``.
"""

from magi.bus.jobs.protocols.channels import Channel

__all__ = ["base", "Channel"]

