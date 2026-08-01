"""Telegram channel adapter — implements :class:`ChannelAdapter`
for the Telegram IM channel. The ONLY file outside the bot's
own update handler that talks the python-telegram-bot client
API; everything else goes through the dispatcher.

Adapter contract — see ``magi.channels.dispatcher``:

  - ``name`` is ``"telegram"``.
  - ``send(uid, text)`` looks up the user's bound TG chat id
    via ``UserImBinding`` (channel='telegram') and calls
    ``bot.send_message(chat_id=<tgid>, text=text)`` via the
    registered :func:`get_telegram_bot` instance.
  - ``lookup_im_id(uid)`` reads the binding row, returns the
    TG chat id as a string (the column is ``BigInteger`` but
    the dispatcher surface is string-typed).
  - ``bind_im_id(uid, im_id)`` writes a new row. The
    Contact.telegram_id column is kept in sync (the legacy
    cache; see D.29+ for removing it once all reads go
    through the dispatcher).
  - ``unbind_im_id(uid)`` drops the binding row + clears
    the Contact.telegram_id cache.

The adapter is registered into the dispatcher at module
import time (see ``channels/telegram/__init__.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading

from sqlalchemy import select

from magi.db import Contact, open_session
from magi.channels import Channel
from magi.channels.dispatcher import (
    ChannelAdapter,
    register_adapter,
)
from magi.channels.telegram import bot as tg_bot_module

logger = logging.getLogger("magi.channels.telegram.adapter")


# Process-wide binding-write lock. Two concurrent bind calls
# for the same (uid, channel) would race on the unique
# constraint — this serialises them so the user sees the
# second request land cleanly rather than tripping an
# IntegrityError that the API then has to translate.
_BIND_LOCK = threading.Lock()


class TelegramAdapter:
    """Channel adapter for Telegram.

    Holds no state besides the lock above; ``bot`` is fetched
    fresh on each ``send`` call via :func:`get_telegram_bot`
    so the daemon thread can replace the instance at runtime
    without the dispatcher noticing.
    """

    name: str = Channel.TG

    async def send(self, uid: int, text: str) -> None:
        im_id = self.lookup_im_id(uid)
        if im_id is None:
            raise RuntimeError(
                f"telegram adapter: uid={uid} has no TG binding"
            )
        try:
            chat_id_int = int(im_id)
        except (TypeError, ValueError) as e:
            raise RuntimeError(
                f"telegram adapter: uid={uid} binding "
                f"is not numeric ({im_id!r})"
            ) from e
        # Always go through ``send_text_auto`` (raw HTTP).
        #
        # Why not ``bot.send_message``: the bot instance
        # lives on the daemon thread's asyncio loop. Calling
        # it from the webui's loop (or any non-daemon loop)
        # silently drops the call — the python-telegram-bot
        # ``Bot.send_message`` is bound to the loop that
        # built it, and the HTTP request goes through a
        # session that the daemon loop owns. The dispatcher
        # tests faked the global bot and so masked the bug;
        # in production the call goes to the daemon-bound
        # bot and the request vanishes.
        #
        # The raw HTTP path (``send_text_auto``) reads the
        # token from settings and posts straight to
        # ``api.telegram.org`` via a fresh httpx client —
        # loop-agnostic, and the log line at the bottom of
        # ``_send_via_raw_http`` records the TG-assigned
        # ``message_id`` so the operator can verify delivery
        # against Telegram's server logs.
        await tg_bot_module.send_text_auto(chat_id_int, text)

    def lookup_im_id(self, uid: int) -> str | None:
        with open_session() as db:
            contact = db.get(Contact, uid)
        if contact is None or contact.telegram_id is None:
            return None
        return str(contact.telegram_id)

    def bind_im_id(self, uid: int, im_id: str) -> None:
        with _BIND_LOCK:
            with open_session() as db:
                contact = db.get(Contact, uid)
                if contact is None:
                    return
                try:
                    contact.telegram_id = int(im_id)
                except (TypeError, ValueError):
                    contact.telegram_id = None
                db.commit()

    def unbind_im_id(self, uid: int) -> None:
        with _BIND_LOCK:
            with open_session() as db:
                contact = db.get(Contact, uid)
                if contact is not None:
                    contact.telegram_id = None
                db.commit()


# Module-import-time registration. Tests that don't want the
# adapter registered can ``dispatcher._ADAPTERS.clear()`` in a
# fixture — but the default path keeps the dispatcher usable
# in any process that imports ``magi.channels.telegram``.
register_adapter(TelegramAdapter())


__all__ = ["TelegramAdapter"]
