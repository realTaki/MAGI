"""Startup-owned construction and lifecycle for the MAGI worker pool."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from magi.runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.old_bus import Bus

logger = logging.getLogger("magi.startup.workers")

#: WebUI powers the operator dashboard and cannot be disabled. A2A is not a
#: channel worker; AgentWorker consumes its MAGIS-shared boards directly.
_REQUIRED_CHANNELS: frozenset[str] = frozenset({"webui"})


class WorkerRegistry:
    """The sole owner of one process' runtime-worker instances."""

    def __init__(
        self,
        bus: Bus,
        *,
        enabled_channels: Iterable[str] = (),
        magi_id: int | None = None,
        worker_concurrency: Mapping[str, int | None] | None = None,
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
        concurrency = dict(worker_concurrency or {})

        def worker_slots(name: str) -> int | None:
            return concurrency.get(name)

        # The WebUI is started regardless of ``enabled_channels``.  The
        # runtime fallback in :func:`magi.startup.runtime._build_channels`
        # provides that safe operator surface when no selection is persisted.
        enabled.update(_REQUIRED_CHANNELS)
        self._workers: dict[str, RuntimeWorker] = {
            "providers": ProvidersWorker(bus, concurrency=worker_slots("providers")),
            "tools": ToolsWorker(bus, concurrency=worker_slots("tools")),
            "mcp": McpWorker(bus, concurrency=worker_slots("mcp")),
            # AgentWorker now receives ``magi_id`` so :meth:`_system_prompt`
            # can render the per-MAGI ``## MAGIS: ... Team instructions``
            # block via :meth:`MagisMembershipBook.instruction_context`.
            "agent": AgentWorker(bus, magi_id=magi_id, concurrency=worker_slots("agent")),
            "task": TaskWorker(bus, concurrency=worker_slots("task")),
            "tg": TelegramWorker(bus, concurrency=worker_slots("tg")),
            "webui": WebUIWorker(bus, concurrency=worker_slots("webui")),
            "proactive": ProactiveWorker(
                bus,
                magi_id=magi_id,
                concurrency=worker_slots("proactive"),
            ),
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
            # Capability discovery happens at runtime startup, before the
            # enabled subset is evaluated.  This makes a newly installed TG
            # adapter visible to the channel API even when it has no token
            # yet and therefore has no running loop.
            for name in ("task", "tg", "webui"):
                worker = self._workers[name]
                register = getattr(worker, "register_channel", None)
                if register is not None:
                    await register()
            for name in ("providers", "tools", "mcp", "agent"):
                await self.start_worker(name)
            for name in ("task", "tg", "webui"):
                aliases = {"task": {"task"}, "tg": {"tg"}}
                if name == "task" or self._enabled_channels & aliases.get(name, {name}):
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
