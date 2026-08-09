"""MAGI capability layer — tools, Skills and MCP integration.

See :mod:`magi.tools.registry` for the public
entry point. Tools are imported lazily to keep cold-start
fast and to support per-test patching.

Lifecycle
=========

The composition root (see :mod:`magi.startup.runtime`) calls
:func:`start_tool_worker` with a fully-wired :class:`Bus`.
The worker publishes the builtin tool catalog at startup and
then drains :class:`RunToolJob` claims forever; :func:`stop_tool_worker`
cancels the run loop. The bus tool worker has been
deprecated — it was deleted from this package's start path;
agent enqueues on the bus, which means
agent tool calls won't fire until the agent migrates too.
"""

from magi.tools.worker import (
    ToolsWorker,
    start_tool_worker,
    stop_tool_worker,
)

__all__ = [
    "ToolsWorker",
    "start_tool_worker",
    "stop_tool_worker",
]
