"""WebUIWorker — WebUI 通道出站 Worker。

纯出站：从 delivery_job_board 认领 channel=="webui" 的 Job，
将消息内容追加到对应 Session（通过 messages_book）。

入站由 FastAPI ``/chat/send`` 路由（``magi/channels/api/chat.py``）处理，
不在本 worker 范围内。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.deliveryJob import DeliveryJob

logger = logging.getLogger("magi.channels.webui.worker")


class WebUIWorker(ChannelWorker):
    """WebUI 通道 Worker：认领 deliveryJob(channel=webui) → Session 追加。
    """

    channel_name = "webui"

    async def _run(self) -> None:
        await self._claim_delivery_loop(self._deliver_webui, "webui")

    async def _deliver_webui(self, job: DeliveryJob) -> None:
        """将 delivery 内容追加到 Session 消息。"""
        session_id = str(job.payload.get("session_id") or "")
        uid = job.payload.get("uid")
        text = str(job.payload.get("text") or "")

        if not session_id or not isinstance(uid, int):
            raise ValueError("webui delivery missing session_id or uid")

        self.bus.messages_book.add(
            session_id=session_id,
            role="assistant",
            text=text,
        )
        logger.debug(
            "WebUIWorker: appended message to session %s (uid=%s)",
            session_id, uid,
        )
