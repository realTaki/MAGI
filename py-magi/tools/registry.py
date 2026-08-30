"""Tool registry — the in-process map of *executable* Tool
instances the tools worker dispatches to.

This is **not** the agent-visible catalog. The catalog lives on
BUS ``ToolsBook`` and is seeded by the worker through
``SetToolJob``. This module owns the dispatch half: a cache of
:class:`~tools.BaseTool.BaseTool` instances.

Builtin tools are hard-coded here. External subsystems (MCP,
skills) inject additional tools at runtime through the public
injection API.

Imports are lazy: each builtin tool is imported on first call
to :func:`builtin_catalog` / :func:`_build_tools`, not at
module load time.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.BaseTool import BaseTool

logger = logging.getLogger("tools.registry")

#: Single-shot cache of builtin :class:`BaseTool` instances — the
#: dispatch backend. Populated lazily on the first
#: :func:`get_tool` call.
_tools_cache: list[BaseTool] | None = None

#: Runtime-injected tools, keyed by source name (e.g. ``"mcp"``).
#: Each call to :func:`register_tools` replaces the entire slot for
#: that source.
_injected: dict[str, list[BaseTool]] = {}

#: Change listeners — fired after :func:`register_tools`.
#: The worker uses this to republish the tool catalog.
_listeners: list[Callable[[], None]] = []


_bus = None

#: ``(module, class)`` for every builtin tool. Catalog seeding reads
#: class fields; ``run`` is not called from here.
_BUILTIN_TOOLS: tuple[tuple[str, str], ...] = (
    ("tools.filesystem.read_file", "ReadFileTool"),
    ("tools.filesystem.write_file", "WriteFileTool"),
    ("tools.filesystem.edit_file", "EditFileTool"),
    ("tools.filesystem.list_files", "ListFilesTool"),
    ("tools.memory.conversations.search_conversations", "SearchConversationsTool"),
    ("tools.comms.send_message", "SendMessageTool"),
    ("tools.comms.message_magi", "MessageMagiTool"),
    ("tools.tasks.schedule", "ScheduleTaskTool"),
    ("tools.shell.run", "BashRunTool"),
    ("tools.shell.output", "BashOutputTool"),
    ("tools.shell.kill", "BashKillTool"),
    ("tools.skills.load_skill", "LoadSkillTool"),
    ("tools.memory.core_memory.add_memory", "AddMemoryTool"),
    ("tools.memory.core_memory.update_memory", "UpdateMemoryTool"),
    ("tools.memory.core_memory.complete_memory", "CompleteMemoryTool"),
    ("tools.memory.core_memory.delete_memory", "DeleteMemoryTool"),
    ("tools.memory.contacts.add_contact", "AddContactTool"),
    ("tools.memory.contacts.add_contact_note", "AddContactNoteTool"),
    ("tools.memory.contacts.update_contact_note", "UpdateContactNoteTool"),
    ("tools.memory.contacts.delete_contact_note", "DeleteContactNoteTool"),
    ("tools.memory.contacts.search_contacts", "SearchContactsTool"),
    ("tools.memory.contacts.update_daily_note", "UpdateDailyNoteTool"),
    ("tools.tasks.add_action_item", "AddActionItemTool"),
    ("tools.tasks.complete_action_item", "CompleteActionItemTool"),
    ("tools.tasks.list_action_items", "ListActionItemsTool"),
    ("tools.mcp.add_mcp_server", "AddMcpServerTool"),
    ("tools.mcp.list_mcp_servers", "ListMcpServersTool"),
    ("tools.mcp.update_mcp_server", "UpdateMcpServerTool"),
    ("tools.mcp.delete_mcp_server", "DeleteMcpServerTool"),
)


def configure(*, bus=None) -> None:
    """Bind the Runtime BUS used when builtin tools are instantiated."""
    global _tools_cache, _bus
    _bus = bus
    _tools_cache = None


def builtin_catalog() -> list[dict[str, Any]]:
    """Agent-visible fields of every builtin tool. Does not run them."""
    entries: list[dict[str, Any]] = []
    for module_name, class_name in _BUILTIN_TOOLS:
        spec = _catalog_spec(module_name, class_name)
        if spec is not None:
            entries.append(spec)
    return entries


def _catalog_spec(module_name: str, class_name: str) -> dict[str, Any] | None:
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except Exception as exc:  # noqa: BLE001 -- one missing tool must not block seeding
        logger.warning("tools catalog: skip %s.%s (%s)", module_name, class_name, exc)
        return None
    name = getattr(cls, "name", "") or ""
    if not name:
        return None
    description = cls.__dict__.get("description", "")
    if not isinstance(description, str):
        try:
            description = str(cls(bus=None).description)
        except Exception:  # noqa: BLE001 -- catalog can ship without a live description
            description = ""
    return {
        "name": name,
        "description": description,
        "input_schema": dict(getattr(cls, "input_schema", None) or {}),
    }


def _build_tools() -> list[BaseTool]:
    """Construct one instance of every builtin tool.

    Importing inside the function (not at module top)
    keeps import-time cheap and lets a test replace one
    tool without dragging in the rest.

    MCP server management tools ARE builtin — administrative
    surface. The MCP worker only injects discovered tools
    under source ``"mcp"``.
    """
    kw = {"bus": _bus}
    tools: list[BaseTool] = []
    for module_name, class_name in _BUILTIN_TOOLS:
        cls = getattr(importlib.import_module(module_name), class_name)
        tools.append(cls(**kw))
    return tools


# -- public API -----------------------------------------------------------


def get_tool(name: str) -> BaseTool | None:
    """Look up a single tool by name for dispatch.

    Searches builtin tools first, then injected sources.
    Returns ``None`` if no such tool is registered.

    Role visibility lives on the catalog, not here.
    """
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = _build_tools()

    for t in _tools_cache:
        if t.name == name:
            return t
    for tools in _injected.values():
        for t in tools:
            if t.name == name:
                return t
    return None


def register_tools(source: str, tools: list[BaseTool]) -> None:
    """Register (or replace) tools from an external source.

    *source* is a stable identifier like ``"mcp"`` or
    ``"skills"``.  Subsequent calls with the same *source*
    replace the previous set.  Fires :func:`on_tools_changed`
    listeners so the worker can republish the catalog.

    External subsystems call this at init time (or whenever
    their tool set changes — e.g. an MCP server is added or
    removed).
    """
    _injected[source] = list(tools)
    logger.info(
        "tools registry: source %r registered %d tool(s)",
        source,
        len(tools),
    )
    _fire_listeners()


def on_tools_changed(callback: Callable[[], None]) -> None:
    """Register a listener that fires when injected tools change.

    The worker uses this to detect new/removed tools and
    republish the catalog.  Callbacks are synchronous and
    should not block — the worker's listener just sets a flag
    that the claim loop picks up on its next iteration.
    """
    _listeners.append(callback)


def list_injected() -> dict[str, list[BaseTool]]:
    """Return a shallow copy of the current injected-tool map.

    The worker calls this to build :class:`ToolDefinition`
    rows for the catalog.
    """
    return dict(_injected)


# -- internal -------------------------------------------------------------


def _fire_listeners() -> None:
    for cb in _listeners:
        try:
            cb()
        except Exception:
            logger.exception(
                "tools registry: listener %r failed",
                cb,
            )


__all__ = [
    "get_tool",
    "configure",
    "builtin_catalog",
    "register_tools",
    "on_tools_changed",
    "list_injected",
]
