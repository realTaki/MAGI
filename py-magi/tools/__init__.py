"""MAGI capability layer — tools, Skills and MCP integration.

See :mod:`tools.registry` for the dispatch map. Lifecycle of
:class:`ToolsWorker` is owned by the MAGI service.
"""

from tools.worker import ToolsWorker

__all__ = [
    "ToolsWorker",
]
