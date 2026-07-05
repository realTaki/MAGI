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
    from magi.runtime.tools.base import Tool

logger = logging.getLogger("magi.runtime.tools.registry")

# Single-shot cache so we don't re-instantiate the tool
# classes on every chat turn. The cache lives for the
# process lifetime; tests that want a fresh set use
# ``reset_cache()``.
_tools_cache: list["Tool"] | None = None


def _build_tools() -> list["Tool"]:
    """Construct one instance of every v0 tool.

    Importing inside the function (not at module top)
    keeps import-time cheap and lets a test replace one
    tool without dragging in the rest.
    """
    from magi.runtime.tools.list_files import ListFilesTool
    from magi.runtime.tools.read_file import ReadFileTool
    from magi.runtime.tools.search_sessions import SearchSessionsTool
    from magi.runtime.tools.send_message import SendMessageTool
    from magi.runtime.tools.write_file import WriteFileTool

    return [
        ReadFileTool(),
        WriteFileTool(),
        ListFilesTool(),
        SearchSessionsTool(),
        SendMessageTool(),
    ]


def get_tools() -> list["Tool"]:
    """Return all v0 tools (cached after first call)."""
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = _build_tools()
    return _tools_cache


def get_tool(name: str) -> "Tool | None":
    """Look up a single tool by name. ``None`` if no such
    tool is registered — the agent loop turns that into
    an ``is_error=true`` ``tool_result`` for the LLM."""
    for t in get_tools():
        if t.name == name:
            return t
    return None


def get_tool_schemas() -> list[dict]:
    """Schemas (Anthropic-shaped) for every registered
    tool — passed straight to ``provider.chat(tools=...)``.

    The list order is stable (it's the order
    :func:`_build_tools` constructs), so the LLM sees the
    same menu every turn.
    """
    return [t.to_anthropic_schema() for t in get_tools()]


def reset_cache() -> None:
    """Drop the cached tool instances. Test-only — lets a
    monkeypatched tool class show up in :func:`get_tools`
    on the next call. Production code never calls this."""
    global _tools_cache
    _tools_cache = None