"""Telegram channel worker: bridge Telegram chats to conversation Jobs."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from contextlib import suppress

from bus import (
    BaseWorker,
    Bus,
    ChatNotify,
    DeliveryNotify,
    DeliveryNotifyResult,
    GetSettingJob,
    JobStatus,
    go,
)

from .bot import send_text_raw

logger = logging.getLogger("channels.telegram.worker")

_BOT_TOKEN_KEY = "telegram.bot_token"


class TelegramWorker(BaseWorker):
    """Receive Telegram messages and deliver conversation replies through Telegram."""

    worker_name = "tg"

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._bot_app: object | None = None
        self._listen: Future[None] | None = None

    async def on_attached(self) -> None:
        token = await self._bot_token()
        if not token:
            logger.info("TelegramWorker: no bot_token; inbound skipped")
            return
        ready = asyncio.Event()
        listen = go(self._listen_bot(token, ready))
        self._listen = listen
        connected = go(ready.wait())
        await asyncio.wait(
            {asyncio.wrap_future(listen), asyncio.wrap_future(connected)},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not connected.done():
            connected.cancel()
        if listen.done():
            listen.result()
        listen.add_done_callback(_report_listener_exit)

    async def on_detached(self) -> None:
        listen = self._listen
        self._listen = None
        if listen is not None and not listen.done():
            listen.cancel()
        if listen is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.wrap_future(listen)

    async def _poll(self) -> bool:
        board = self.board(DeliveryNotify)
        if board is None:
            return False
        job = await self.call(board.claim_for_channel, "tg")
        if job is None:
            return False
        go(self._deliver(job))
        return True

    async def _listen_bot(self, token: str, ready: asyncio.Event) -> None:
        from telegram.ext import Application, MessageHandler, filters

        app = (
            Application.builder()
            .token(token)
            .concurrent_updates(True)
            .connect_timeout(15)
            .read_timeout(15)
            .write_timeout(15)
            .pool_timeout(5)
            .build()
        )
        app.add_handler(MessageHandler(filters.ALL, self._on_tg_message))
        self._bot_app = app
        try:
            await app.initialize()
            await app.start()
            updater = app.updater
            if updater is None:
                raise RuntimeError("Telegram Application.updater is None after start()")
            await updater.start_polling(poll_interval=1.0, timeout=10)
            ready.set()
            await asyncio.Event().wait()
        finally:
            updater = app.updater
            if updater is not None:
                with suppress(Exception):
                    await updater.stop()
            with suppress(Exception):
                await app.stop()
            with suppress(Exception):
                await app.shutdown()
            self._bot_app = None

    async def _on_tg_message(self, update, _context) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        text = (message.text or "").strip()
        if not text:
            return
        await self.call(self._ingest, str(chat.id), text)

    def _ingest(self, delivery_address: str, text: str) -> None:
        self.publish(
            ChatNotify(
                publisher=self.worker_name,
                channel="tg",
                delivery_address=delivery_address,
                text=text,
            )
        )

    async def _deliver(self, job: DeliveryNotify) -> None:
        token = await self._bot_token()
        try:
            chat_id = int(job.address or "")
        except (TypeError, ValueError):
            chat_id = 0
        if job.channel != "tg" or not chat_id or not job.text or not token:
            await self._submit_delivery(
                DeliveryNotifyResult(
                    id=job.id,
                    status=JobStatus.FAILED,
                    error="no Telegram chat for this conversation",
                )
            )
            return
        try:
            await send_text_raw(token, chat_id, job.text)
        except Exception as exc:  # noqa: BLE001 -- delivery failure belongs to its Job
            await self._submit_delivery(
                DeliveryNotifyResult(id=job.id, status=JobStatus.FAILED, error=str(exc))
            )
            return
        await self._submit_delivery(DeliveryNotifyResult(id=job.id))

    async def _submit_delivery(self, result: DeliveryNotifyResult) -> None:
        self.submit(DeliveryNotify, result)

    async def _bot_token(self) -> str:
        token = (self._settings.get("bot_token") or "").strip()
        if token:
            return token
        got = await self.ask(
            GetSettingJob(publisher=self.worker_name, key=_BOT_TOKEN_KEY)
        )
        return "" if got is None or got.value is None else got.value.strip()


def _report_listener_exit(future: Future[None]) -> None:
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.error("Telegram listener stopped", exc_info=exc)
