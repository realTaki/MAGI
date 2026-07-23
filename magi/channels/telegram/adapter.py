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
    Employee.telegram_id column is kept in sync (the legacy
    cache; see D.29+ for removing it once all reads go
    through the dispatcher).
  - ``unbind_im_id(uid)`` drops the binding row + clears
    the Employee.telegram_id cache.

The adapter is registered into the dispatcher at module
import time (see ``channels/telegram/__init__.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading

from sqlalchemy import select

from magi.agent.db import open_session
from magi.agent.db.models_user_im_binding import UserImBinding
from magi.channels.dispatcher import (
    ChannelAdapter,
    register_adapter,
)
from magi.channels.telegram.bot import get_telegram_bot

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

    name: str = "telegram"

    async def send(self, uid: int, text: str) -> None:
        bot = get_telegram_bot()
        if bot is None:
            raise RuntimeError(
                "telegram adapter: no bot registered; "
                "is the TG channel running?"
            )
        im_id = self.lookup_im_id(uid)
        if im_id is None:
            raise RuntimeError(
                f"telegram adapter: uid={uid} has no TG binding"
            )
        # python-telegram-bot's vendor kwarg is ``chat_id=``;
        # everything upstream uses the abstract name.
        try:
            chat_id_int = int(im_id)
        except (TypeError, ValueError) as e:
            raise RuntimeError(
                f"telegram adapter: uid={uid} binding "
                f"is not numeric ({im_id!r})"
            ) from e
        await bot.send_message(chat_id=chat_id_int, text=text)

    def lookup_im_id(self, uid: int) -> str | None:
        with open_session() as db:
            row = db.get(UserImBinding, (uid, "telegram"))
        if row is None:
            return None
        return row.im_id

    def bind_im_id(self, uid: int, im_id: str) -> None:
        with _BIND_LOCK:
            with open_session() as db:
                existing = db.get(UserImBinding, (uid, "telegram"))
                if existing is None:
                    db.add(UserImBinding(
                        uid=uid, channel="telegram", im_id=im_id,
                    ))
                else:
                    existing.im_id = im_id
                # Keep Employee.telegram_id in sync — legacy
                # read paths (and the bot's update handler)
                # still read that column. Drop it in a future
                # C8 cleanup once all reads go through the
                # dispatcher.
                emp = db.scalar(
                    select(__import__(
                        "magi.agent.db", fromlist=["Employee"]
                    ).Employee).where(
                        __import__(
                            "magi.agent.db", fromlist=["Employee"]
                        ).Employee.id == uid
                    )
                )
                if emp is not None:
                    try:
                        emp.telegram_id = int(im_id)
                    except (TypeError, ValueError):
                        emp.telegram_id = None
                db.commit()

    def unbind_im_id(self, uid: int) -> None:
        with _BIND_LOCK:
            with open_session() as db:
                row = db.get(UserImBinding, (uid, "telegram"))
                if row is not None:
                    db.delete(row)
                # Sync the legacy column.
                from magi.agent.db import Employee
                emp = db.get(Employee, uid)
                if emp is not None:
                    emp.telegram_id = None
                db.commit()


# Module-import-time registration. Tests that don't want the
# adapter registered can ``dispatcher._ADAPTERS.clear()`` in a
# fixture — but the default path keeps the dispatcher usable
# in any process that imports ``magi.channels.telegram``.
register_adapter(TelegramAdapter())


__all__ = ["TelegramAdapter"]
