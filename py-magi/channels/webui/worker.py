"""WebUIWorker — WebUI 通道出站 Worker。

纯 wire 投递 — 从 delivery_notify_job_board 认领 channel=="webui" 的 Job，
把消息内容通过 WebSocket 推到对应 Session。

``chat_messages`` 行的写入由 :meth:`deliveryNotifyJobBoard.publish` 在
入队时完成（与 ``chatNotifyBoard.publish`` 写用户行对称），本 worker
不再触 ``messages_book``。如果 wire 投递失败，delivery notify 的 result
会记 :attr:`JobStatus.FAILED`，但 assistant 行已经在 DB 里 ——
transcript 反映"agent 说了什么"，而不是"线路上确认了什么"。

入站由 FastAPI ``/chat/send`` 路由（``magi/channels/api/chat.py``）处理，
不在本 worker 范围内。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from old_bus.firmwares.jobs.deliveryNotifyJob import DeliveryNotifyJob

logger = logging.getLogger("channels.webui.worker")


class WebUIWorker(ChannelWorker):
    """WebUI 通道 Worker：认领 delivery notify(channel=webui) → WS 推送。"""

    channel_name = "webui"

    async def _run(self) -> None:
        await self._claim_delivery_loop(self._deliver_webui, "webui")

    async def _deliver_webui(self, job: DeliveryNotifyJob) -> None:
        """推送 delivery 内容到对应 WebUI Session（纯 wire 投递）。"""
        conversation_id = job.conversation_id or 0
        contact_id = job.contact_id

        if not conversation_id or not isinstance(contact_id, int):
            raise ValueError("webui delivery missing conversation_id or contact_id")

        # The ``chat_messages`` row is written by
        # :meth:`deliveryNotifyJobBoard.publish` at enqueue time. This
        # method only handles the WS push; a failure here surfaces
        # as :attr:`JobStatus.FAILED` on submit_result but does
        # NOT delete the assistant row.
        logger.debug(
            "WebUIWorker: pushing message to conversation %s (contact_id=%s)",
            conversation_id,
            contact_id,
        )
