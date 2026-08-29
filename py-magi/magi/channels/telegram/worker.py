"""TelegramWorker — TG 入站长轮询 + 出站投递。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.old_bus.firmwares.books.local import Contact, Role
from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.old_bus import Bus
    from magi.old_bus.firmwares.jobs.deliveryNotifyJob import DeliveryNotifyJob

logger = logging.getLogger("magi.channels.telegram.worker")


class TelegramWorker(ChannelWorker):
    channel_name = "tg"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        delivery_poll_seconds: float = 0.1,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds, concurrency=concurrency)
        self._delivery_poll_seconds = delivery_poll_seconds
        self._bot_app: object | None = None
        self._shutdown_event: asyncio.Event | None = None

    async def on_start(self) -> bool:
        await super().on_start()
        bot_token = await self.call(self.bus.settings_book.get_value, key="telegram.bot_token")
        if not bot_token:
            logger.info("TelegramWorker: no bot_token; skipping")
            return False
        return True

    async def _run(self) -> None:
        await asyncio.gather(self._run_inbound(), self._run_outbound())

    async def _run_inbound(self) -> None:
        from telegram.ext import Application, MessageHandler, filters

        token = await self.call(self.bus.settings_book.get_value, key="telegram.bot_token")
        if not token:
            return
        app = (
            Application.builder()
            .token(str(token))
            .concurrent_updates(True)
            .connect_timeout(15)
            .read_timeout(15)
            .write_timeout(15)
            .pool_timeout(5)
            .build()
        )
        app.add_handler(MessageHandler(filters.ALL, self._on_tg_message))
        self._bot_app = app
        self._shutdown_event = asyncio.Event()
        try:
            await app.initialize()
            await app.start()
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
                try:
                    await updater.stop()
                except Exception:
                    pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            self._bot_app = None

    async def _on_tg_message(self, update, _context) -> None:
        if update.effective_chat is None or update.effective_message is None:
            return
        tgid = str(update.effective_chat.id)
        text = update.effective_message.text or ""
        contact = _resolve_contact(self.bus, tgid)
        if contact is None:
            await _send_stranger_reply(update, tgid, self.bus)
            return
        contact_id, role, contact_name = contact
        if role != Role.ASSIGNED:
            await update.effective_message.reply_text(
                f"你的账号（{contact_name}）不属于本 MAGI 服务范围。\n"
                f"请联系 MAGI 管理员，或把你的 ID ({tgid}) 告诉他们以加入。"
            )
            return
        if not text.strip():
            await update.effective_message.reply_text("我暂时只支持文字消息，等 C4 加上多模态再试。")
            return
        conversation_id = _resolve_tg_session(self.bus, contact_id=contact_id, tgid=tgid)
        # The user message is persisted to ``chat_messages`` inside
        # :meth:`chatNotifyBoard.publish` — see ``magi.bus.firmwares.jobs.chatNotifyJob``.
        # Channels must not reach into ``messages_book`` directly anymore.
        from magi.old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob

        try:
            job_id = self.bus.agent_job_board.publish(
                ChatNotifyJob(
                    text=text,
                    channel="tg",
                    contact_id=contact_id,
                    conversation_id=conversation_id,
                )
            )
            # A Telegram update reaching this adapter is not an Agent read.
            # Wait for the durable PROCESSING receipt before reacting or
            # advertising that the Agent is typing.
            asyncio.create_task(
                _await_agent_receipt(
                    update,
                    job_id=job_id,
                    bus=self.bus,
                )
            )
        except Exception:
            logger.exception("TelegramWorker: publish ChatNotifyJob failed for tgid=%s", tgid)

    async def _run_outbound(self) -> None:
        await self._claim_delivery_loop(self._deliver_tg, "tg")

    async def _deliver_tg(self, job: DeliveryNotifyJob) -> None:
        bot_token = await self.call(self.bus.settings_book.get_value, key="telegram.bot_token")
        if not bot_token:
            raise RuntimeError("Telegram delivery: no bot_token")
        chat_id = int(job.destination) if job.destination else 0
        text = job.text
        if not chat_id or not text:
            raise ValueError("TG delivery missing destination or text")
        from magi.channels.telegram.bot import send_text_raw

        await send_text_raw(str(bot_token), chat_id, text)

    async def on_stop_requested(self) -> None:
        if self._shutdown_event is not None:
            self._shutdown_event.set()


def _resolve_contact(bus: Bus, tgid: str) -> tuple[int, str, str] | None:
    try:
        cid_int = int(tgid)
    except (TypeError, ValueError):
        return None
    contact = bus.contacts_book.get_by_telegram(tgid=cid_int)
    if contact is None:
        return None
    # Telegram contacts are MAGI-local, so only the local ``role`` is resolved
    # here. MAGIS administrator identity lives on ``magis_admins`` and is
    # not inferred from a local Contact's TG binding.
    name = (contact.display_name or contact.name or f"stranger-{tgid[-5:]}").strip()
    return (contact.id, contact.role, name)


def _resolve_tg_session(bus: Bus, *, contact_id: int, tgid: str) -> int:
    session = bus.conversations_book.get_or_create_for_tg(
        contact_id=contact_id,
        delivery_address=tgid,
    )
    return session.id


# Note: the user message is persisted to ``chat_messages`` inside
# :meth:`chatNotifyBoard.publish` (see ``magi.bus.firmwares.jobs.chatNotifyJob``).
# Channels must not reach into ``messages_book`` directly anymore —
# the chokepoint lives in the bus layer where the cap, D.22 guard,
# and chatNotifyJob enqueue are all atomic.


async def _send_stranger_reply(update, tgid: str, bus: Bus) -> None:
    display_name = (
        update.effective_chat.first_name
        or update.effective_chat.username
        or update.effective_chat.title
    )
    name = (display_name or "").strip() or f"stranger-{tgid[-5:]}"
    try:
        cid_int = int(tgid)
    except (TypeError, ValueError):
        cid_int = 0
    try:
        if bus.contacts_book.get_by_telegram(tgid=cid_int) is None:
            bus.contacts_book.add(
                Contact(name=name, display_name=display_name, role=Role.GUEST, tgid=cid_int)
            )
    except Exception:
        pass
    await update.effective_message.reply_text(
        "👋 Hi — you're not in MAGI's super-admin list yet.\n\n"
        f"Your ID is: <code>{tgid}</code>\n\n"
        "Please contact the MAGI admin and share this ID so they can add your "
        "permissions. Once that's done, message me anything and I'll route you "
        "to the right person.",
        parse_mode="HTML",
    )


async def _send_read_receipt(update, bus: Bus) -> None:
    try:
        from magi.channels.telegram.config import get_read_reaction_emoji

        reaction = get_read_reaction_emoji(bus)
        if reaction:
            await update.get_bot().set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.effective_message.message_id,
                reaction=reaction,
            )
    except Exception:
        pass


async def _await_agent_receipt(update, *, job_id: int, bus: Bus) -> None:
    """Fire the read reaction the moment the Agent durably claims the turn.

    Poll :meth:`chatNotifyBoard.check_job_status` until the row leaves
    ``PENDING``. Any non-pending state — ``PROCESSING`` (claimed), or
    a terminal ``COMPLETED``/``FAILED`` that raced past between polls —
    proves the Agent has the message and triggers the single read
    reaction. Once fired, the watcher exits; we don't track the
    "done" milestone here. A terminal race is benign: the row may
    pass through PROCESSING between polls, but as soon as we observe
    any non-pending state we react once and return.
    """
    from magi.old_bus.bases.job import JobStatus

    # 120 × 0.25s = 30s ceiling for an Agent to claim the turn.
    # Beyond that we give up silently — no reaction is better than a
    # reaction that lags the user's follow-up message.
    for _ in range(120):
        try:
            status = await asyncio.to_thread(
                bus.agent_job_board.check_job_status,
                job_id=job_id,
            )
        except Exception:
            return
        if status is None:
            return
        if status != JobStatus.PENDING:
            await _send_read_receipt(update, bus)
            return
        await asyncio.sleep(0.25)
