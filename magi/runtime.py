"""Process composition for MAGI workers.

This module is intentionally outside ``agent``, ``tools`` and ``channels``.
It is the only runtime lifecycle adapter that knows concrete workers; channel
applications attach it as an ASGI lifespan without importing agent/tool code.
"""

from __future__ import annotations

from contextlib import asynccontextmanager


@asynccontextmanager
async def worker_lifespan():
    """Run local durable workers for the lifetime of an ASGI process."""
    from magi.agent.worker import start_agent_worker, stop_agent_worker
    from magi.channels.delivery import start_delivery_worker, stop_delivery_worker
    from magi.tools.worker import start_tool_worker, stop_tool_worker

    await start_agent_worker()
    await start_tool_worker()
    await start_delivery_worker()
    try:
        yield
    finally:
        await stop_delivery_worker()
        await stop_tool_worker()
        await stop_agent_worker()
