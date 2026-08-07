
"""Tool registry — the in-process map of *executable* Tool
instances the tools worker dispatches to.

This is **not** the agent-visible catalog. The catalog (what
the LLM sees as its menu) lives in the new_bus
:mod:`magi.new_bus.library.local.toolsBook` and is fed by
worker startup via :func:`magi.tools.worker._publish_builtin_catalog`.
This module owns only the dispatch half: a cache of
:class:`~magi.tools.base.Tool` instances, plus the MCP
loader that appends to it. When :func:`get_tool` looks up a
tool by name, it walks this cache — there is no
``get_tools()`` / ``get_tool_schemas()`` here anymore, by
design: agent-side menu reads go to the Book, not here.

v0 hard-codes the builtin set here. When ``skill_loader``
(D.17) lands, skills get appended to this list at runtime
based on the deployer's config; the registry API stays the
same so the worker doesn't have to grow with it.

Imports are lazy: each tool is imported on first call to
:func:`_build_tools`, not at module load time. That's how
tests can patch one tool (``monkeypatch.setattr``) without
triggering the rest of the registry's side-effects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.tools.base import Tool

logger = logging.getLogger("magi.tools.registry")

# Single-shot cache of builtin :class:`Tool` instances — the
# dispatch backend. Populated lazily on the first
# :func:`get_tool` / :func:`bootstrap_mcp_tools` call.
# Lives for the process lifetime; tests that need a fresh
# set either ``del magi.tools.registry._tools_cache`` or
# restart the process.
_tools_cache: list["Tool"] | None = None

# MCP tools are loaded once at boot via
# :func:`bootstrap_mcp_tools` and appended on top of
# ``_tools_cache``. A separate slot keeps the two surfaces
# (built-in tools / MCP-discovered tools) distinct so the
# MCP connection isn't re-opened on a builtin reload. The
# agent loop never reads this directly; :func:`get_tool`
# walks builtin first, then MCP.
_mcp_tools_cache: list["Tool"] | None = None

# Stamp of the most recent successful MCP load —
# specifically, the ``max(updated_at)`` observed across
# the ``mcp_servers`` table at load time. Compared on
# every chat turn by :func:`maybe_reload_mcp_tools`: a
# mismatch means an operator edited (added / removed /
# toggled / deleted) a server since the last load and
# the cache is stale. ``None`` means "never loaded",
# which forces a reload on the first chat turn.
_mcp_loaded_at_db = None


def _build_tools() -> list["Tool"]:
    """Construct one instance of every v0 tool.

    Importing inside the function (not at module top)
    keeps import-time cheap and lets a test replace one
    tool without dragging in the rest.

    Returned list is the dispatch order (builtin tools in
    the order they appear here). MCP tools are appended
    on top by :func:`bootstrap_mcp_tools`. Tool worker
    claim paths consult :func:`get_tool`, which walks
    builtin first then MCP.
    """
    from magi.tools.shell.run import BashRunTool
    from magi.tools.shell.output import BashOutputTool
    from magi.tools.shell.kill import BashKillTool
    from magi.tools.tasks.action_item import (
        AddActionItemTool,
        CompleteActionItemTool,
        ListActionItemTool,
    )
    from magi.tools.filesystem.edit_file import EditFileTool
    from magi.tools.filesystem.list_files import ListFilesTool
    from magi.tools.comms.message_magi import MessageMagiTool
    from magi.mcp.manage import (
        AddMcpServerTool,
        DeleteMcpServerTool,
        ListMcpServersTool,
        UpdateMcpServerTool,
    )
    from magi.tools.filesystem.read_file import ReadFileTool
    from magi.tools.tasks.schedule import ScheduleTaskTool
    from magi.skills.loader_tool import SkillLoaderTool
    from magi.tools.memory.sessions import SearchSessionsTool
    from magi.tools.comms.send_message import SendMessageTool
    from magi.tools.filesystem.write_file import WriteFileTool
    from magi.tools.memory.contacts import (
        AddContactNoteTool,
        AddContactTool,
        DeleteContactNoteTool,
        SearchContactsTool,
        UpdateContactNoteTool,
        UpdateDailyNoteTool,
    )
    from magi.tools.memory.self import (
        AddMemoryTool,
        CompleteMemoryTool,
        DeleteMemoryTool,
        UpdateMemoryTool,
    )

    return [
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListFilesTool(),
        SearchSessionsTool(),
        SendMessageTool(),
        MessageMagiTool(),
        ScheduleTaskTool(),
        SkillLoaderTool(),
        # Shell execution — three tools the LLM uses
        # together: ``bash`` runs (foreground or
        # background), ``bash_output`` polls a
        # background process, ``bash_kill`` terminates.
        BashRunTool(),
        BashOutputTool(),
        BashKillTool(),
        # Memory management — LLM-driven, not auto.
        # The operator must say "记住 X" (or the LLM
        # must judge the fact long-arc enough) for
        # these to fire.
        AddMemoryTool(),
        UpdateMemoryTool(),
        CompleteMemoryTool(),
        DeleteMemoryTool(),
        # Contact directory — what the MAGI knows
        # about people. Operator-driven, not auto.
        AddContactTool(),
        AddContactNoteTool(),
        UpdateContactNoteTool(),
        DeleteContactNoteTool(),
        SearchContactsTool(),
        UpdateDailyNoteTool(),
        # Action item — per-contact, scoped to the
        # caller. ALLOWED_ROLES = {admin, assigned}
        # keeps these out of the menu for other roles;
        # the in-run ``_gate`` on each tool is the
        # second-layer defence.
        AddActionItemTool(),
        CompleteActionItemTool(),
        ListActionItemTool(),
        # MCP server management — admin-only.
        # LLM-side CRUD lives in :mod:`magi.mcp.manage`;
        # the registry imports them from the MCP package
        # (top-level ``mcp/`` subsystem, not under
        # ``tools/``).
        AddMcpServerTool(),
        ListMcpServersTool(),
        UpdateMcpServerTool(),
        DeleteMcpServerTool(),
    ]


def get_tool(
    name: str,
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> "Tool | None":
    """Look up a single tool by name for dispatch.

    Returns ``None`` if no such tool is registered, or if
    the tool is gated by ``ALLOWED_ROLES`` and the caller
    doesn't match — the worker turns that into an
    ``is_error=true`` tool_result for the LLM.

    Role-gated lookup honors ``caller_role`` / ``caller_admin``
    the same way the old :func:`get_tools` filter did. The
    cache is initialized lazily here (was previously
    initialized inside :func:`get_tools`).
    """
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = _build_tools()
    for t in _tools_cache:
        if t.name == name:
            return t if t.is_allowed_for_role(caller_role, admin=caller_admin) else None
    for t in (_mcp_tools_cache or []):
        if t.name == name:
            return t if t.is_allowed_for_role(caller_role, admin=caller_admin) else None
    return None


def bootstrap_mcp_tools() -> list["Tool"]:
    """One-shot MCP loader used by :mod:`magi.__main__` at startup.

    Sync from the caller's POV — it runs the asyncio
    bootstrap in a private event loop and returns the
    discovered tools (also cached so subsequent
    :func:`get_tool` calls see them).

    The loader reads from the ``mcp_servers`` table — the
    table is the only source of truth (the legacy
    ``mcp.json`` flow is gone). The table is read every
    time this function runs, including on a lazy reload
    triggered by :func:`maybe_reload_mcp_tools`.

    Errors degrade to "no MCP tools". The boot never fails
    because MCP didn't make it through. See
    ``load_mcp_tools_blocking`` for the loop mechanics.
    """
    global _mcp_tools_cache, _mcp_loaded_at_db
    from magi.mcp.loader import load_mcp_tools_blocking

    tools = load_mcp_tools_blocking()
    _mcp_tools_cache = list(tools)
    # Stamp the cache with the table's max ``updated_at``
    # so a subsequent :func:`maybe_reload_mcp_tools` can
    # detect a stale cache without a monotonic-float
    # comparison. ``None`` when the table is empty — the
    # "did the table change?" check below handles that
    # path explicitly.
    try:
        _mcp_loaded_at_db = get_bus().mcp.revision_stamp()
    except Exception:
        # Don't let a DB read failure poison the cache
        # load. The next chat turn will retry the stamp
        # read; until then the cache is fresh from the
        # loader's POV.
        _mcp_loaded_at_db = None
    if tools:
        logger.info("MCP bootstrap registered %d tool(s): %s",
                    len(tools), ", ".join(t.name for t in tools))
    return tools


def maybe_reload_mcp_tools() -> list["Tool"] | None:
    """Re-bootstrap the MCP cache if the table changed.

    Called when the tool worker starts. Cheap when the table is
    untouched — a single ``SELECT MAX(updated_at) FROM
    mcp_servers`` query, no reconnect, no subprocess.

    Returns the freshly-loaded list when a reload fired
    (or an empty list when the table is empty after the
    reload) so the caller can log "MCP reloaded with N
    tools". Returns ``None`` when the cache was up to date
    — no logging, no churn.
    """
    try:
        latest = get_bus().mcp.revision_stamp()
    except Exception:
        # Missing table (pre-init) or DB hiccup — leave
        # the cache alone. The next chat turn will try
        # again, and the boot path (``init_orm``)
        # guarantees the table exists before the first
        # turn anyway.
        return None

    latest_stamp = latest

    if latest_stamp == _mcp_loaded_at_db:
        # No row has been touched since the last load
        # AND the cache is fresh. ``_mcp_tools_cache``
        # might still be ``None`` if the last reload
        # produced zero tools — that's fine, we treat
        # the empty-table case as "no edit needed"
        # too.
        return None

    # Either the table is now non-empty (was empty at
    # last load) OR the latest ``updated_at`` has moved
    # forward. Either way: reload. Bootstrap logs the
    # new tool count; we just return the list so the
    # caller can plumb a debug log.
    return bootstrap_mcp_tools()
