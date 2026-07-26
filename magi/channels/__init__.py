"""Channel adapters for MAGI.

A channel receives inbound messages from a user surface (Telegram chat,
WebUI console, future email / calendar) and sends outbound messages back.
Both Adam and EVE mount one or more channels and feed messages into the
same ``magi.agent`` agent loop.

``channels/base.py`` defines the abstract ``Channel`` interface
(receive / send / identify_sender). Concrete adapters:

- ``channels.telegram`` — EVE side, python-telegram-bot v21+ (C3).
- ``channels.webui``    — Adam side, FastAPI + HTMX + WS (C1 for CRUD, C7 for chat console).
"""

from enum import StrEnum

__all__ = ["base", "Channel"]


class Channel(StrEnum):
    """Typed channel identifiers used across the agent loop,
    session store, and dispatcher.

    Replaces free-form ``channel: str`` with a compile-time-
    checkable enum. A typo like ``"telegram"`` instead of
    ``"tg"`` is now caught by the type checker.
    """

    TG = "tg"
    """Telegram bot channel (EVE → user)."""

    WEBUI = "webui"
    """WebUI chat console (ADAM → operator)."""

    SCHEDULED = "scheduled"
    """Proactive runner — fires tools on a cron schedule."""