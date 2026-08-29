"""MAGI capability layer — tools, Skills and MCP integration.

See :mod:`magi.tools.registry` for the public
entry point. Tools are imported lazily to keep cold-start
fast and to support per-test patching.

Lifecycle is owned by :class:`magi.startup.workers.WorkerRegistry`.
"""

from magi.tools.worker import ToolsWorker

__all__ = [
    "ToolsWorker",
]
