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

__all__ = ["base"]