"""ProactiveWorker — 最后一个拉起的 Worker，处理系统级主动策略。

启动流程
========

1. ``_bootstrap()`` — 判断本 MAGI 是否是所处 MAGIS 的 Adam；
   若是，对已有 admin 幂等插入 credentials nudge。
2. ``_run()`` — 主循环，claim SeedPresetTasksJob 并执行播种。

Policy 逻辑委托给同包下的独立模块：
- :mod:`magi.proactive.credentials_action` — credentials nudge spec + 幂等插入
- :mod:`magi.proactive.preset_tasks` — preset 任务播种
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.proactive.worker")


class ProactiveWorker:
    """系统级主动策略的消费者。

    Receives a fully-wired :class:`Bus` and the current
    ``magi_id`` via constructor injection.
    """

    def __init__(
        self,
        bus: "Bus",
        *,
        magi_id: int | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        self.bus = bus
        self._magi_id = magi_id
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return

        # 1. Bootstrap: Adam 检查 + admin credentials nudge
        await self._bootstrap()

        # 2. 启动主循环
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="magi-proactive-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        from magi.proactive.preset_tasks import handle_seed_job

        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.seed_preset_tasks_job_board.claim,
                )
            except Exception:
                logger.exception("proactive worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            await handle_seed_job(self.bus, job)

    # ------------------------------------------------------------------
    # bootstrap
    # ------------------------------------------------------------------

    async def _bootstrap(self) -> None:
        """启动时：如果本 MAGI 是 Adam，对已有 admin 幂等插入 credentials nudge。"""
        from magi.proactive.credentials_action import ensure_for_admin

        magis_id = self._resolve_magis_id()
        if magis_id is None:
            return  # 没有 MAGIS DB，跳过

        if not self._is_adam(magis_id):
            logger.info("proactive worker: not Adam, skipping bootstrap")
            return

        magis_admins_book = getattr(self.bus, "magis_admins_book", None)
        if magis_admins_book is None:
            return
        admin_rows = magis_admins_book.list_for_magis(magis_id=magis_id)
        if not admin_rows:
            logger.info(
                "proactive worker: no admins for magis_id=%d, skipping", magis_id
            )
            return

        for entry in admin_rows:
            uid = entry.uid  # contacts.id
            inserted = ensure_for_admin(
                book=self.bus.action_items_book,
                admin_id=uid,
            )
            if inserted:
                logger.info(
                    "proactive worker: bootstrap nudge inserted for admin uid=%d",
                    uid,
                )

    # ------------------------------------------------------------------
    # MAGIS identity
    # ------------------------------------------------------------------

    def _resolve_magis_id(self) -> int | None:
        """解析本 MAGI 所属的 MAGIS id。"""
        magi_id = self._magi_id
        if magi_id is None:
            return None
        memberships_book = getattr(self.bus, "memberships_book", None)
        if memberships_book is None:
            return None
        membership = memberships_book.get(magi_id=magi_id)
        if membership is None:
            return None
        return membership.magis_id

    def _is_adam(self, magis_id: int) -> bool:
        """判断本 MAGI 是否是给定 MAGIS 的 Adam。"""
        magi_id = self._magi_id
        if magi_id is None:
            return False
        magis_book = getattr(self.bus, "magis_book", None)
        if magis_book is None:
            return False
        magis_node = magis_book.get(magis_id=magis_id)
        if magis_node is None:
            return False
        return magis_node.adam_id == magi_id


# -- module-level singleton ------------------------------------------------


_worker: ProactiveWorker | None = None


async def start_proactive_worker(
    bus: "Bus",
    *,
    magi_id: int | None = None,
) -> ProactiveWorker:
    """Start the process-local proactive worker.

    ``magi_id`` is the per-MAGI identity; ``None`` skips
    Adam-dependent bootstrap checks.
    """
    global _worker
    if _worker is None:
        _worker = ProactiveWorker(bus=bus, magi_id=magi_id)
        await _worker.start()
    return _worker


async def stop_proactive_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


__all__ = [
    "ProactiveWorker",
    "start_proactive_worker",
    "stop_proactive_worker",
]
