"""Telegram channel adapter — implements :class:`ChannelAdapter`."""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from magi.channels import Channel, get_current_new_bus
from magi.channels.dispatcher import (
    ChannelAdapter,
    register_adapter,
)
from magi.channels.telegram import bot as tg_bot_module

logger = logging.getLogger("magi.channels.telegram.adapter")

_BIND_LOCK = threading.Lock()


class TelegramAdapter:
    name: str = Channel.TG

    async def send(self, uid: int, text: str) -> None:
        im_id = self.lookup_im_id(uid)
        if im_id is None:
            raise RuntimeError(f"telegram adapter: uid={uid} has no TG binding")
        try:
            chat_id_int = int(im_id)
        except (TypeError, ValueError) as e:
            raise RuntimeError(
                f"telegram adapter: uid={uid} binding is not numeric ({im_id!r})"
            ) from e
        await tg_bot_module.send_text_auto(chat_id_int, text)

    def lookup_im_id(self, uid: int) -> str | None:
        nb = get_current_new_bus()
        if nb is None:
            return None
        contact = nb.contacts_book.get(contact_id=uid)
        if contact is None or contact.telegram_id is None:
            return None
        return str(contact.telegram_id)

    def bind_im_id(self, uid: int, im_id: str) -> None:
        with _BIND_LOCK:
            try:
                telegram_id = int(im_id)
            except (TypeError, ValueError):
                telegram_id = None
            nb = get_current_new_bus()
            if nb is not None:
                nb.contacts_book.set_telegram_id(uid, telegram_id)

    def unbind_im_id(self, uid: int) -> None:
        with _BIND_LOCK:
            nb = get_current_new_bus()
            if nb is not None:
                nb.contacts_book.set_telegram_id(uid, None)


register_adapter(TelegramAdapter())

__all__ = ["TelegramAdapter"]
