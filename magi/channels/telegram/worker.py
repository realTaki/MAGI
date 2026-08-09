"""TelegramWorker — TG 入站长轮询 + 出站投递。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.deliveryJob import DeliveryJob

logger = logging.getLogger("magi.channels.telegram.worker")


class TelegramWorker(ChannelWorker):
    channel_name = "tg"

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25, delivery_poll_seconds: float = 0.1) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._delivery_poll_seconds = delivery_poll_seconds
        self._bot_app: object | None = None
        self._shutdown_event: asyncio.Event | None = None

    async def on_start(self) -> bool:
        bot_token = await self.call(self.bus.settings_book.get, key="telegram.bot_token")
        if not bot_token:
            logger.info("TelegramWorker: no bot_token; skipping")
            return False
        return True

    async def _run(self) -> None:
        await asyncio.gather(self._run_inbound(), self._run_outbound())

    async def _run_inbound(self) -> None:
        from telegram.ext import Application, MessageHandler, filters
        token = await self.call(self.bus.settings_book.get, key="telegram.bot_token")
        if not token: return
        app = (Application.builder().token(str(token)).concurrent_updates(True)
               .connect_timeout(15).read_timeout(15).write_timeout(15).pool_timeout(5).build())
        app.add_handler(MessageHandler(filters.ALL, self._on_tg_message))
        self._bot_app = app; self._shutdown_event = asyncio.Event()
        try:
            await app.initialize(); await app.start()
            # ``Application.updater`` is typed ``Updater | None``; the
            # lib populates it during ``start()``. Hoist it once and
            # guard so the rest of the function deals with a concrete
            # ``Updater`` (Pylance narrows it from the assertion).
            updater = app.updater
            if updater is None:
                raise RuntimeError(
                    "TelegramWorker inbound: Application.updater is None after start(); "
                    "python-telegram-bot version mismatch?"
                )
            await updater.start_polling(poll_interval=1.0, timeout=10)
            await self._shutdown_event.wait()
        except RuntimeError as exc:
            logger.warning("TelegramWorker inbound: %s", exc)
        finally:
            # ``app.updater`` may be None if ``start()`` failed before
            # populating it; the lib's own ``app.stop()`` / ``app.shutdown()``
            # already guard on this, so we mirror the same check before
            # calling ``updater.stop()`` to avoid a runtime crash.
            updater = app.updater
            if updater is not None:
                try: await updater.stop()
                except Exception: pass
            try: await app.stop()
            except Exception: pass
            try: await app.shutdown()
            except Exception: pass
            self._bot_app = None

    async def _on_tg_message(self, update, context) -> None:
        if update.effective_chat is None or update.effective_message is None: return
        tgid = str(update.effective_chat.id); text = update.effective_message.text or ""
        contact = _resolve_contact(self.bus, tgid)
        if contact is None:
            await _send_stranger_reply(update, tgid, self.bus); return
        contact_id, role, is_admin = contact
        if not (is_admin or role == "assigned"):
            await update.effective_message.reply_text(f"TG ID: {tgid}. Ask your admin for access."); return
        if not text.strip():
            await update.effective_message.reply_text("MAGI currently only handles text messages."); return
        session_id = _resolve_tg_session(self.bus, contact_id=contact_id, tgid=tgid)
        _append_user_message(self.bus, session_id, text)
        asyncio.create_task(_send_read_receipt(update, self.bus))
        from magi.bus.guild.chatJob import publish_chat
        try:
            publish_chat(
                self.bus, text=text, channel="tg", contact_id=contact_id, session_id=session_id,
                caller_role=role, event_id=f"telegram:{tgid}:{update.effective_message.message_id}",
                chat_id=tgid, tg_message_id=update.effective_message.message_id,
            )
        except Exception:
            logger.exception("TelegramWorker: publish ChatJob failed for tgid=%s", tgid)

    async def _run_outbound(self) -> None:
        await self._claim_delivery_loop(self._deliver_tg, "tg")

    async def _deliver_tg(self, job: DeliveryJob) -> None:
        bot_token = await self.call(self.bus.settings_book.get, key="telegram.bot_token")
        if not bot_token: raise RuntimeError("Telegram delivery: no bot_token")
        chat_id = int(job.destination) if job.destination else 0
        text = str(job.payload.get("text") or "")
        if not chat_id or not text: raise ValueError("TG delivery missing destination or text")
        from magi.channels.telegram.bot import send_text_raw
        await send_text_raw(str(bot_token), chat_id, text)

    async def on_stop_requested(self) -> None:
        if self._shutdown_event is not None: self._shutdown_event.set()


def _resolve_contact(bus: Bus, tgid: str) -> tuple[int, str, bool] | None:
    try: cid_int = int(tgid)
    except (TypeError, ValueError): return None
    contact = bus.contacts_book.get_by_telegram(telegram_id=cid_int)
    if contact is None: return None
    return (contact.id, contact.role, contact.admin)


def _resolve_tg_session(bus: Bus, *, contact_id: int, tgid: str) -> str:
    session = bus.sessions_book.get_or_create_for_channel(
        contact_id=contact_id, channel="tg", delivery_address=tgid,
    )
    return session.session_id


def _append_user_message(bus: Bus, session_id: str, text: str) -> None:
    try: bus.messages_book.add(session_id=session_id, role="user", text=text)
    except Exception: pass


async def _send_stranger_reply(update, tgid: str, bus: Bus) -> None:
    display_name = update.effective_chat.first_name or update.effective_chat.username or update.effective_chat.title
    name = (display_name or "").strip() or f"stranger-{tgid[-5:]}"
    try: cid_int = int(tgid)
    except: cid_int = 0
    try:
        if bus.contacts_book.get_by_telegram(telegram_id=cid_int) is None:
            bus.contacts_book.add(name=name, display_name=display_name, role="guest", telegram_id=cid_int)
    except Exception: pass
    await update.effective_message.reply_text(f"Your Telegram ID is {tgid}. Ask your admin to add you.")


async def _send_read_receipt(update, bus: Bus) -> None:
    try:
        from magi.channels.telegram.config import get_read_reaction_emoji
        reaction = get_read_reaction_emoji(bus)
        if reaction: await update.get_bot().set_message_reaction(chat_id=update.effective_chat.id, message_id=update.effective_message.message_id, reaction=reaction)
    except Exception: pass
