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

    async def start(self) -> None:
        bot_token = self.bus.settings_book.get(key="telegram.bot_token")
        if not bot_token:
            logger.info("TelegramWorker: no bot_token; skipping")
            return
        await super().start()

    async def _run(self) -> None:
        await asyncio.gather(self._run_inbound(), self._run_outbound())

    async def _run_inbound(self) -> None:
        from telegram.ext import Application, MessageHandler, filters
        token = self.bus.settings_book.get(key="telegram.bot_token")
        if not token: return
        app = (Application.builder().token(str(token)).concurrent_updates(True)
               .connect_timeout(15).read_timeout(15).write_timeout(15).pool_timeout(5).build())
        app.add_handler(MessageHandler(filters.ALL, self._on_tg_message))
        self._bot_app = app; self._shutdown_event = asyncio.Event()
        try:
            await app.initialize(); await app.start()
            await app.updater.start_polling(poll_interval=1.0, timeout=10)
            await self._shutdown_event.wait()
        except RuntimeError as exc:
            logger.warning("TelegramWorker inbound: %s", exc)
        finally:
            try: await app.updater.stop()
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
        uid, role, is_admin = contact
        if not (is_admin or role == "assigned"):
            await update.effective_message.reply_text(f"TG ID: {tgid}. Ask your admin for access."); return
        if not text.strip():
            await update.effective_message.reply_text("MAGI currently only handles text messages."); return
        session_id = _resolve_tg_session(self.bus, uid=uid, tgid=tgid)
        _append_user_message(self.bus, session_id, text)
        asyncio.create_task(_send_read_receipt(update))
        from magi.bus.guild.chatJob import ChatJob
        try:
            self.bus.agent_job_board.publish(ChatJob(
                event_id=f"telegram:{tgid}:{update.effective_message.message_id}", kind="chat",
                payload={"text": text, "channel": "tg", "uid": uid, "session_id": session_id,
                         "tg_chat_id": tgid, "tg_message_id": update.effective_message.message_id, "caller_role": role},
            ))
        except Exception:
            logger.exception("TelegramWorker: publish ChatJob failed for tgid=%s", tgid)

    async def _run_outbound(self) -> None:
        await self._claim_delivery_loop(self._deliver_tg, "tg")

    async def _deliver_tg(self, job: DeliveryJob) -> None:
        bot_token = self.bus.settings_book.get(key="telegram.bot_token")
        if not bot_token: raise RuntimeError("Telegram delivery: no bot_token")
        chat_id = int(job.destination) if job.destination else 0
        text = str(job.payload.get("text") or "")
        if not chat_id or not text: raise ValueError("TG delivery missing destination or text")
        from magi.channels.telegram.bot import send_text_raw
        await send_text_raw(str(bot_token), chat_id, text)

    async def stop(self) -> None:
        if self._shutdown_event is not None: self._shutdown_event.set()
        await super().stop()


def _resolve_contact(bus: Bus, tgid: str) -> tuple[int, str, bool] | None:
    try: cid_int = int(tgid)
    except (TypeError, ValueError): return None
    contact = bus.contacts_book.get_by_telegram(telegram_id=cid_int)
    if contact is None: return None
    return (contact.id, contact.role, contact.admin)


def _resolve_tg_session(bus: Bus, *, uid: int, tgid: str) -> str:
    import uuid; from datetime import datetime, timezone
    sessions = bus.sessions_book.list_for_owner(uid=uid)
    tg_sessions = [s for s in sessions if getattr(s, "channel", "") == "tg"]
    if tg_sessions: return tg_sessions[0].session_id
    now = datetime.now(timezone.utc).isoformat(); sid = f"tg_{uuid.uuid4().hex[:16]}"
    bus.sessions_book.add(session_id=sid, delivery_address=tgid, uid=uid, channel="tg", created_at=now, updated_at=now)
    return sid


def _append_user_message(bus: Bus, session_id: str, text: str) -> None:
    import uuid; from datetime import datetime, timezone
    try: bus.messages_book.add(session_id=session_id, message_id=uuid.uuid4().hex, role="user", text=text, ts=datetime.now(timezone.utc).isoformat())
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


async def _send_read_receipt(update) -> None:
    try:
        from magi.channels.telegram.config import get_read_reaction_emoji
        reaction = get_read_reaction_emoji()
        if reaction: await update.get_bot().set_message_reaction(chat_id=update.effective_chat.id, message_id=update.effective_message.message_id, reaction=reaction)
    except Exception: pass
