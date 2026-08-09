"""WebUIWorker — 出站 DeliveryJob(channel=webui) → Session 追加。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.deliveryJob import DeliveryJob

logger = logging.getLogger("magi.channels.webui.worker")


class WebUIWorker(ChannelWorker):
    channel_name = "webui"

    async def _run(self) -> None:
        await self._claim_delivery_loop(self._deliver_webui, "webui")

    async def _deliver_webui(self, job: DeliveryJob) -> None:
        session_id = str(job.payload.get("session_id") or "")
        uid = job.payload.get("uid")
        text = str(job.payload.get("text") or "")
        if not session_id or not isinstance(uid, int):
            raise ValueError("webui delivery missing session_id or uid")
        self.bus.messages_book.add(session_id=session_id, role="assistant", text=text)
