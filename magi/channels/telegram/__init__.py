"""Telegram channel — bot, dispatcher adapter, and helpers.

D.28 collapsed all TG-specific knowledge into this package:

  - ``bot.py``    — the python-telegram-bot listener + the
    inbound ``_on_message`` handler. Reads ``tgid`` from
    the incoming Update.
  - ``adapter.py`` — the dispatcher-registered channel
    adapter. Reads ``tgid`` from ``user_im_bindings`` (or the
    legacy ``Employee.telegram_id`` cache) and pushes via
    ``bot.send_message(chat_id=<tgid>, text=...)``.
  - ``config.py``  — the bot token / runtime config.

Importing this package registers the TG adapter with the
dispatcher, so callers don't have to. ``from magi.channels
import dispatcher`` then dispatches TG via ``send_to_session``
automatically — no explicit wiring needed at the call site.
"""

from __future__ import annotations

from magi.channels.telegram.adapter import TelegramAdapter
from magi.channels.telegram.bot import (
    _handle_employee_message,
    _on_message,
    clear_telegram_bot,
    get_telegram_bot,
    set_telegram_bot,
    start_bot,
)
from magi.channels.dispatcher import register_adapter

# Side-effect: register the TG adapter into the dispatcher.
# Importing this package is enough to make
# ``from magi.channels import dispatcher; dispatcher.send_to_session(...)``
# route through the TG adapter for ``channel="tg"`` sessions.
register_adapter(TelegramAdapter())


__all__ = [
    # Adapter
    "TelegramAdapter",
    # Bot lifecycle
    "start_bot",
    "stop_bot",
    "set_telegram_bot",
    "clear_telegram_bot",
    "get_telegram_bot",
    # Inbound handlers (referenced by start_bot's message handler)
    "_on_message",
    "_handle_employee_message",
]