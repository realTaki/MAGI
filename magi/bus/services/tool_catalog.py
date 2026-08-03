"""Bus service: tool_catalog (DB-backed tool definitions for the agent).

The agent's tool schemas come from this service, NOT from the in-process
:mod:`magi.tools.registry`.  The in-process registry remains for tool
worker execution only; this service is the durable, role-filtered,
revision-tracked source of truth.
"""

from __future__ import annotations

# Re-export the existing implementation verbatim.  The full file stays
# at :mod:`magi.bus.tool_catalog` for now so the existing call sites
# (bootstrap, tests) keep working without a churn pass.  The service
# surface (``ToolCatalogService``) is what callers use.
from magi.bus.tool_catalog import (
    CatalogRevisionConflict,
    ToolCatalogService,
    ToolCatalogSnapshot,
    ToolCatalogValidationError,
    ToolDefinition,
)


__all__ = [
    "CatalogRevisionConflict",
    "ToolCatalogService",
    "ToolCatalogSnapshot",
    "ToolCatalogValidationError",
    "ToolDefinition",
]
