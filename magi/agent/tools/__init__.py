"""Deprecated compatibility namespace for :mod:`magi.tools`.

Tool implementations belong to the top-level capability layer.  This package
only preserves old import paths for third-party extensions during migration;
new code must import from ``magi.tools``.
"""

import importlib
import sys


_MOVED_MODULES = (
    "_safe_path",
    "action_item",
    "base",
    "bash",
    "edit_file",
    "edit_retry",
    "list_files",
    "mcp_loader",
    "mcp_manage",
    "read_file",
    "registry",
    "schedule_task",
    "search_sessions",
    "send_message",
    "services_stub",
    "skill_loader",
    "skill_loader_tool",
    "write_file",
)

# Alias each legacy submodule to the *same* module object.  A path-only
# redirect would load, for example, both ``magi.tools.base`` and
# ``magi.agent.tools.base``; then ``ToolResult`` type checks and singleton
# registries split in two.
for _name in _MOVED_MODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(
        f"magi.tools.{_name}"
    )
