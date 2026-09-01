"""Executable tool lookup and runtime injection.

The agent-visible catalog belongs to BUS ``ToolsBook``; this module only
keeps the in-process instances dispatched by the tools worker. Builtins are
loaded lazily, while MCP and similar subsystems replace their own source slot.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.BaseTool import BaseTool

logger = logging.getLogger("tools.registry")

BuiltinTool = tuple[str, str]

#: Builtin dispatch backend, populated on the first :func:`get_tool` call.
_tools_cache: list[BaseTool] | None = None

#: Runtime-injected tools, keyed by source name. Registration replaces a slot.
_injected: dict[str, list[BaseTool]] = {}

#: Listeners fired after a source slot is replaced.
_listeners: list[Callable[[], None]] = []

_bus = None

#: ``(module, class)`` for each builtin. Catalog seeding reads class fields.
_BUILTIN_TOOLS: tuple[BuiltinTool, ...] = (
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
    """Bind the BUS passed to newly constructed builtin tools."""
    global _tools_cache, _bus
    _bus = bus
    _tools_cache = None


def builtin_catalog() -> list[dict[str, Any]]:
    """Agent-visible fields of every builtin tool. Does not run them."""
    entries: list[dict[str, Any]] = []
    for builtin in _BUILTIN_TOOLS:
        spec = _catalog_spec(builtin)
        if spec is not None:
            entries.append(spec)
    return entries


def _catalog_spec(builtin: BuiltinTool) -> dict[str, Any] | None:
    module_name, class_name = builtin
    try:
        cls = _load_builtin(builtin)
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
    """Construct builtin dispatch instances on demand.

    Unlike catalog seeding, dispatch fails loudly if a declared builtin cannot
    be imported. MCP management is builtin; only discovered MCP tools are
    injected under the ``"mcp"`` source.
    """
    return [_load_builtin(builtin)(bus=_bus) for builtin in _BUILTIN_TOOLS]


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


def _load_builtin(builtin: BuiltinTool) -> type[BaseTool]:
    """Import one declared builtin without instantiating it."""
    module_name, class_name = builtin
    return getattr(importlib.import_module(module_name), class_name)


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
