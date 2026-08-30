"""Tool registry — the in-process map of *executable* Tool
instances the tools worker dispatches to.

This is **not** the agent-visible catalog. The catalog (what
the LLM sees as its menu) lives in the bus
:mod:`bus.firmwares.books.local.toolsBook` and is fed by
the worker via :meth:`ToolsWorker._publish_full_catalog`.
This module owns only the dispatch half: a cache of
:class:`~tools.BaseTool.BaseTool` instances.

Builtin tools are hard-coded here. External subsystems (MCP,
skills) inject additional tools at runtime through the public
injection API.  The worker subscribes via
:func:`on_tools_changed` and republishes the full catalog
whenever injected tools change.

Imports are lazy: each builtin tool is imported on first call
to :func:`_build_tools`, not at module load time. That's how
tests can patch one tool (``monkeypatch.setattr``) without
triggering the rest of the registry's side-effects.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

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


def configure(*, bus=None) -> None:
    """Bind the Runtime BUS used when builtin tools are instantiated."""
    global _tools_cache, _bus
    _bus = bus
    _tools_cache = None


def _build_tools() -> list[BaseTool]:
    """Construct one instance of every builtin tool.

    Importing inside the function (not at module top)
    keeps import-time cheap and lets a test replace one
    tool without dragging in the rest.

    MCP server management tools (``add_mcp_server`` /
    ``list_mcp_servers`` / ``update_mcp_server`` /
    ``delete_mcp_server``) ARE builtin — they're
    administrative surface that should always be available
    when an admin wants to configure MCP servers. The MCP
    worker itself only injects the *discovered* tools under
    source ``"mcp"``; the CRUD tools are registered here.
    """
    from tools.comms.message_magi import MessageMagiTool
    from tools.comms.send_message import SendMessageTool
    from tools.filesystem.edit_file import EditFileTool
    from tools.filesystem.list_files import ListFilesTool
    from tools.filesystem.read_file import ReadFileTool
    from tools.filesystem.write_file import WriteFileTool
    from tools.mcp.add_mcp_server import AddMcpServerTool
    from tools.mcp.delete_mcp_server import DeleteMcpServerTool
    from tools.mcp.list_mcp_servers import ListMcpServersTool
    from tools.mcp.update_mcp_server import UpdateMcpServerTool
    from tools.memory.contacts.add_contact import AddContactTool
    from tools.memory.contacts.add_contact_note import AddContactNoteTool
    from tools.memory.contacts.delete_contact_note import DeleteContactNoteTool
    from tools.memory.contacts.search_contacts import SearchContactsTool
    from tools.memory.contacts.update_contact_note import UpdateContactNoteTool
    from tools.memory.contacts.update_daily_note import UpdateDailyNoteTool
    from tools.memory.core_memory.add_memory import AddMemoryTool
    from tools.memory.core_memory.complete_memory import CompleteMemoryTool
    from tools.memory.core_memory.delete_memory import DeleteMemoryTool
    from tools.memory.core_memory.update_memory import UpdateMemoryTool
    from tools.memory.conversations.search_conversations import SearchConversationsTool
    from tools.shell.kill import BashKillTool
    from tools.shell.output import BashOutputTool
    from tools.shell.run import BashRunTool
    from tools.skills.load_skill import LoadSkillTool
    from tools.tasks.add_action_item import AddActionItemTool
    from tools.tasks.complete_action_item import CompleteActionItemTool
    from tools.tasks.list_action_items import ListActionItemsTool
    from tools.tasks.schedule import ScheduleTaskTool

    kw = {"bus": _bus}
    return [
        ReadFileTool(**kw),
        WriteFileTool(**kw),
        EditFileTool(**kw),
        ListFilesTool(**kw),
        SearchConversationsTool(**kw),
        SendMessageTool(**kw),
        MessageMagiTool(**kw),
        ScheduleTaskTool(**kw),
        BashRunTool(**kw),
        BashOutputTool(**kw),
        BashKillTool(**kw),
        LoadSkillTool(**kw),
        AddMemoryTool(**kw),
        UpdateMemoryTool(**kw),
        CompleteMemoryTool(**kw),
        DeleteMemoryTool(**kw),
        AddContactTool(**kw),
        AddContactNoteTool(**kw),
        UpdateContactNoteTool(**kw),
        DeleteContactNoteTool(**kw),
        SearchContactsTool(**kw),
        UpdateDailyNoteTool(**kw),
        AddActionItemTool(**kw),
        CompleteActionItemTool(**kw),
        ListActionItemsTool(**kw),
        AddMcpServerTool(**kw),
        ListMcpServersTool(**kw),
        UpdateMcpServerTool(**kw),
        DeleteMcpServerTool(**kw),
    ]


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
    "register_tools",
    "on_tools_changed",
    "list_injected",
]
