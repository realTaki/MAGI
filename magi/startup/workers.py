"""Startup-owned construction and lifecycle for the MAGI worker pool."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from magi.bus.library.local.tasksBook import Channel
from magi.runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.startup.workers")

#: WebUI powers the operator dashboard and cannot be disabled. A2A is not a
#: channel worker; AgentWorker consumes its MAGIS-shared boards directly.
_REQUIRED_CHANNELS: frozenset[str] = frozenset({Channel.WEBUI.value})


class WorkerRegistry:
    """The sole owner of one process' runtime-worker instances."""

    def __init__(
        self,
        bus: Bus,
        *,
        enabled_channels: Iterable[str] = (),
        magi_id: int | None = None,
    ) -> None:
        from magi.agent.worker import AgentWorker
        from magi.channels.tasks.worker import TaskWorker
        from magi.channels.telegram.worker import TelegramWorker
        from magi.channels.webui.worker import WebUIWorker
        from magi.mcp.worker import McpWorker
        from magi.proactive.worker import ProactiveWorker
        from magi.providers.worker import ProvidersWorker
        from magi.tools.worker import ToolsWorker

        enabled = set(enabled_channels)
        # Required channels (WebUI + A2A per the 2026-08-10 architecture
        # review) are unconditionally started regardless of the
        # configured ``enabled_channels`` list. This is the
        # composition-root-level counterpart to the
        # ``channels.enabled`` default written by :mod:`magi.bus.provision`
        # and the runtime-side fallback in
        # :func:`magi.startup.runtime._build_channels`.
        enabled.update(_REQUIRED_CHANNELS)
        self._workers: dict[str, RuntimeWorker] = {
            "providers": ProvidersWorker(bus),
            "tools": ToolsWorker(bus),
            "mcp": McpWorker(bus),
            # AgentWorker now receives ``magi_id`` so :meth:`_system_prompt`
            # can render the per-MAGI ``## MAGIS: ... Team instructions``
            # block via :meth:`MagisMembershipBook.instruction_context`.
            "agent": AgentWorker(bus, magi_id=magi_id),
            "task": TaskWorker(bus),
            "tg": TelegramWorker(bus),
            "webui": WebUIWorker(bus),
            "proactive": ProactiveWorker(bus, magi_id=magi_id),
        }
        self._started: list[RuntimeWorker] = []
        self._enabled_channels = enabled

    @property
    def workers(self) -> dict[str, RuntimeWorker]:
        return dict(self._workers)

    def channel_workers(self) -> dict[str, RuntimeWorker]:
        return {
            name: worker
            for name, worker in self._workers.items()
            if worker.worker_kind == "channel"
        }

    def get_worker(self, name: str) -> RuntimeWorker | None:
        """Return a known worker, or ``None`` for an unimplemented channel."""
        return self._workers.get(name)

    def is_running(self, name: str) -> bool:
        worker = self.get_worker(name)
        return bool(worker and worker.health()["running"])

    async def start(self) -> None:
        try:
            for name in ("providers", "tools", "mcp", "agent"):
                await self.start_worker(name)
            for name in ("task", "tg", "webui"):
                aliases = {"task": {"task", "scheduled"}, "tg": {"tg", "telegram"}}
                if self._enabled_channels & aliases.get(name, {name}):
                    await self.start_worker(name)
            await self.start_worker("proactive")
        except Exception:
            await self.stop()
            raise

    async def start_worker(self, name: str) -> bool:
        worker = self._workers[name]
        if worker in self._started:
            return True
        if not await worker.start():
            return False
        self._started.append(worker)
        return True

    async def stop_worker(self, name: str) -> None:
        worker = self._workers[name]
        if worker not in self._started:
            return
        await worker.stop()
        self._started.remove(worker)

    async def stop(self) -> None:
        while self._started:
            worker = self._started.pop()
            try:
                await worker.stop()
            except Exception:
                logger.exception("failed to stop worker %s", worker.worker_name)

    def health(self) -> list[dict[str, object]]:
        return [worker.health() for worker in self._workers.values()]


__all__ = ["WorkerRegistry"]
