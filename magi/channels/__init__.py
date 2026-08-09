"""Channel adapters for MAGI.

A channel receives inbound messages from a user surface (Telegram chat,
WebUI console, future email / calendar) and sends outbound messages back.
Both ADAM and EVA mount one or more channels. They publish messages to the
private ``Bus``; the MAGI-owned agent worker consumes them sequentially.

``channels/base.py`` defines the abstract ``Channel`` interface
(receive / send / identify_sender). ``channels/worker_base.py`` defines
the ``ChannelWorker`` ABC for per-channel Worker implementations.

Concrete adapters:

- ``channels.telegram`` — EVA side, python-telegram-bot v21+ (C3).
- ``channels.webui``    — ADAM side, FastAPI + HTMX + WS (C1 for CRUD, C7 for chat console).

The :class:`Channel` enum is owned by the Bus task domain and re-exported
here as the common channel vocabulary.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.channels.worker_base import ChannelWorker
from magi.bus.library.local.tasksBook import Channel

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.channels")

# Adapter bridge set by the runtime after bus bootstrap. New worker code uses
# explicit constructor injection; this accessor is limited to channel adapters.
_current_bus: Bus | None = None


def set_current_bus(bus: Bus) -> None:
    """Set the process-global bus reference."""
    global _current_bus
    _current_bus = bus


def get_current_bus() -> Bus | None:
    """Return the process-global bus reference, or None."""
    return _current_bus


# ── Channel Worker lifecycle ─────────────────────────────────────────

# Worker singletons
_telegram: object | None = None      # TelegramWorker
_task: object | None = None          # TaskWorker
_webui: object | None = None         # WebUIWorker
_a2a: object | None = None           # A2AWorker

# Process-global registry for health endpoint
_registry: dict[str, ChannelWorker] = {}

# closed set of known channel names
_KNOWN_CHANNELS: frozenset[str] = frozenset({"tg", "webui", "a2a", "scheduled", "telegram", "task"})


async def start_channel_workers(
    bus: Bus,
    *,
    enabled: set[str],
) -> dict[str, ChannelWorker]:
    """启动所有已启用的 Channel Worker。

    未知 channel 名记 warning，不静默跳过。
    返回 {channel_name: worker} 映射供调用方管理生命周期。
    """
    from magi.channels.telegram.worker import TelegramWorker
    from magi.channels.tasks.worker import TaskWorker
    from magi.channels.api.worker import WebUIWorker
    from magi.channels.a2a.worker import A2AWorker

    unknown = enabled - _KNOWN_CHANNELS
    if unknown:
        logger.warning(
            "channels: ignoring unknown enabled names: %s", sorted(unknown),
        )

    global _telegram, _task, _webui, _a2a, _registry

    # Contract order (§5): task → telegram → webui → a2a
    if "scheduled" in enabled or "task" in enabled:
        _task = await _start_one("task", lambda: TaskWorker(bus))

    if "telegram" in enabled or "tg" in enabled:
        _telegram = await _start_one("tg", lambda: TelegramWorker(bus))

    if "webui" in enabled:
        _webui = await _start_one("webui", lambda: WebUIWorker(bus))

    if "a2a" in enabled:
        _a2a = await _start_one("a2a", lambda: A2AWorker(bus))

    return dict(_registry)


async def stop_channel_workers(workers: dict[str, ChannelWorker]) -> None:
    """逆序停止所有 Channel Worker。"""
    for name in reversed(list(workers.keys())):
        try:
            await workers[name].stop()
        except Exception:
            logger.exception("channel worker %s stop failed", name)
    global _registry
    _registry.clear()


def registered_channel_workers() -> dict[str, ChannelWorker]:
    """返回当前注册的所有 Channel Worker 快照。"""
    return dict(_registry)


async def _start_one(name: str, factory) -> ChannelWorker:
    """Start a single worker, register it, return it."""
    global _registry
    try:
        worker = factory()
        await worker.start()
        _registry[name] = worker
        return worker
    except Exception:
        logger.exception("channel worker %s start failed", name)
        raise


__all__ = [
    "base",
    "worker_base",
    "Channel",
    "ChannelWorker",
    "set_current_bus",
    "get_current_bus",
    "start_channel_workers",
    "stop_channel_workers",
    "registered_channel_workers",
]
