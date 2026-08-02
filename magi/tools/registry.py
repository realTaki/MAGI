
"""Tool registry — the single source of truth for which
tools the LLM can call.

v0 hard-codes four tools here. When ``skill_loader`` (D.17)
lands, skills get appended to this list at runtime based
on the deployer's config; the registry API stays the same
so the agent loop doesn't have to grow with it.

Imports are lazy: each tool is imported on first call
to :func:`get_tools`, not at module load time. That's how
tests can patch one tool (``monkeypatch.setattr``) without
triggering the rest of the registry's side-effects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.tools.base import Tool

logger = logging.getLogger("magi.tools.registry")

# Single-shot cache so we don't re-instantiate the tool
# classes on every chat turn. The cache lives for the
# process lifetime; tests that want a fresh set use
# ``reset_cache()``.
_tools_cache: list["Tool"] | None = None

# MCP tools are loaded once at boot via
# :func:`bootstrap_mcp_tools` and appended on top of
# ``_tools_cache``. A separate slot keeps the two surfaces
# (built-in tools / MCP-discovered tools) distinct so a
# ``reset_cache`` call doesn't have to re-connect MCP. The
# agent loop never reads this directly; ``get_tools``
# merges the two lists.
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
    """
    from magi.tools.bash import (
        BashKillTool,
        BashOutputTool,
        BashRunTool,
    )
    from magi.tools.action_item import (
        AddActionItemTool,
        CompleteActionItemTool,
        ListActionItemTool,
    )
    from magi.tools.edit_file import EditFileTool
    from magi.tools.list_files import ListFilesTool
    from magi.tools.message_magi import MessageMagiTool
    from magi.mcp.manage import (
        AddMcpServerTool,
        DeleteMcpServerTool,
        ListMcpServersTool,
        UpdateMcpServerTool,
    )
    from magi.tools.read_file import ReadFileTool
    from magi.tools.schedule_task import ScheduleTaskTool
    from magi.tools.services_stub import (
        ReadRecentEmailsTool,
        ReadUpcomingMeetingsTool,
    )
    from magi.tools.skill_loader_tool import SkillLoaderTool
    from magi.tools.search_sessions import SearchSessionsTool
    from magi.tools.send_message import SendMessageTool
    from magi.tools.write_file import WriteFileTool
    from magi.tools.memory_contacts import (
        AddContactNoteTool,
        AddContactTool,
        DeleteContactNoteTool,
        SearchContactsTool,
        UpdateContactNoteTool,
        UpdateDailyNoteTool,
    )
    from magi.tools.memory_self import (
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
        # Stub external-service tools — read_recent_emails
        # and read_upcoming_meetings return mock data
        # today (real OAuth-backed Gmail / Calendar adapters
        # land in C5). ``ALLOWED_ROLES = frozenset()`` makes
        # them visible to every role so the LLM can quote
        # the placeholder content in casual chat.
        ReadRecentEmailsTool(),
        ReadUpcomingMeetingsTool(),
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


def get_tools(
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> list["Tool"]:
    """Return all registered tools (cached after first call).

    Built-in tools are appended first; MCP tools (loaded at
    boot) come after. Order matters to the LLM (the agent
    loop passes ``schemas`` in order; some models put more
    weight on the first few tools), so MCP belongs at the
    end of the menu.

    ``caller_role`` filters out tools whose
    :attr:`Tool.ALLOWED_ROLES` exclude the caller. ``None``
    (caller didn't supply a role — tests, boot-time probes)
    returns tools with no role restriction; role-restricted
    tools are stripped (better than silently exposing
    admin-only tools to unidentified callers).

    ``caller_admin=True`` is the post-2024 split shortcut:
    a WebUI-signed-in operator sees the full menu regardless
    of their ``role`` enum value. Mirrors the API's
    ``_enforce_creator_can_create`` helper.
    """
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = _build_tools()
    all_tools = _tools_cache + (_mcp_tools_cache or [])
    return [
        t for t in all_tools
        if t.is_allowed_for_role(caller_role, admin=caller_admin)
    ]


def get_tool(
    name: str,
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> "Tool | None":
    """Look up a single tool by name. ``None`` if no such
    tool is registered — the agent loop turns that into
    an ``is_error=true`` ``tool_result`` for the LLM.

    Role-gated lookup honors ``caller_role``: an
    admin-only tool is invisible to a caller who passes
    a different role (matches the menu-filter behaviour
    of :func:`get_tools`).
    """
    for t in get_tools(caller_role=caller_role, caller_admin=caller_admin):
        if t.name == name:
            return t
    return None


def get_tools_grouped(
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> tuple[list["Tool"], list["Tool"]]:
    """Return ``(built_in, mcp)`` lists, both filtered
    through :meth:`Tool.is_allowed_for_role` with
    ``caller_role``.

    Surfaces that want to render the two registries
    distinctly (audit dashboards, status pages,
    :mod:`magi.channels.webui.api.tools`) reach for this
    helper instead of touching :data:`_tools_cache` /
    :data:`_mcp_tools_cache` directly. The MCP cache is
    :data:`None` before :func:`bootstrap_mcp_tools` has
    run; we treat that as "no MCP tools yet" and return
    ``([], [])`` semantics for that side.
    """
    global _tools_cache, _mcp_tools_cache
    if _tools_cache is None:
        _tools_cache = _build_tools()
    built_in = [
        t for t in _tools_cache
        if t.is_allowed_for_role(caller_role, admin=caller_admin)
    ]
    mcp = [
        t for t in (_mcp_tools_cache or [])
        if t.is_allowed_for_role(caller_role, admin=caller_admin)
    ]
    return built_in, mcp


def get_tool_schemas(
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> list[dict]:
    """Schemas (Anthropic-shaped) for every registered
    tool — passed straight to ``provider.chat(tools=...)``.

    The list order is stable (it's the order
    :func:`_build_tools` constructs), so the LLM sees the
    same menu every turn.
    """
    return [
        t.to_anthropic_schema() for t in
        get_tools(caller_role=caller_role, caller_admin=caller_admin)
    ]


def reset_cache() -> None:
    """Drop the cached tool instances. Test-only — lets a
    monkeypatched tool class show up in :func:`get_tools`
    on the next call. Production code never calls this."""
    global _tools_cache
    _tools_cache = None


def bootstrap_mcp_tools() -> list["Tool"]:
    """One-shot MCP loader used by :mod:`magi.__main__` at startup.

    Sync from the caller's POV — it runs the asyncio
    bootstrap in a private event loop and returns the
    discovered tools (also cached so subsequent
    :func:`get_tools` calls reuse them).

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
    from magi.db import McpServer, open_session
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
        with open_session() as s:
            stamp = s.query(McpServer.updated_at).order_by(
                McpServer.updated_at.desc()
            ).first()
        _mcp_loaded_at_db = stamp[0] if stamp is not None else None
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


def reset_mcp_cache() -> None:
    """Drop only the MCP tool cache.

    Unlike :func:`reset_cache` (which wipes the built-in
    tools too), this is used by tests that want to swap the
    MCP config without rebuilding the slow built-in list.

    Also resets the load stamp so the next
    :func:`maybe_reload_mcp_tools` will fire even if the
    test edits the table without bumping ``updated_at``.
    """
    global _mcp_tools_cache, _mcp_loaded_at_db
    _mcp_tools_cache = None
    _mcp_loaded_at_db = None


def maybe_reload_mcp_tools() -> list["Tool"] | None:
    """Re-bootstrap the MCP cache if the table changed.

    Called at the top of every chat turn (see
    :mod:`magi.agent.loop`). Cheap when the table is
    untouched — a single ``SELECT MAX(updated_at) FROM
    mcp_servers`` query, no reconnect, no subprocess.

    Returns the freshly-loaded list when a reload fired
    (or an empty list when the table is empty after the
    reload) so the caller can log "MCP reloaded with N
    tools". Returns ``None`` when the cache was up to date
    — no logging, no churn.
    """
    from magi.db import McpServer, open_session

    try:
        with open_session() as s:
            latest = s.query(McpServer.updated_at).order_by(
                McpServer.updated_at.desc()
            ).first()
    except Exception:
        # Missing table (pre-init) or DB hiccup — leave
        # the cache alone. The next chat turn will try
        # again, and the boot path (``init_orm``)
        # guarantees the table exists before the first
        # turn anyway.
        return None

    latest_stamp = latest[0] if latest is not None else None

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
