"""Tools — list every tool the LLM can invoke.

End-user-facing read-only view of
:meth:`magi.agent.tools.registry.get_tools_grouped`.
Useful for the operator to verify what their MAGI install
can actually do — ``mcp.json`` loaded the right servers,
no LLM tool wedge broke, etc. Also surfaces each tool's
:attr:`magi.agent.tools.base.Tool.ALLOWED_ROLES` so the
operator can audit "who can call what" without reading
code (D.universal-role-gate).

We pull the rich :class:`Tool` instances (not just the
flat schemas) because ``allowed_roles`` lives on the
class, not the wire-format schema. ``source`` (builtin
vs MCP) is also tracked here so the dashboard can render
two distinct cards — the operator usually cares whether
a tool ships with MAGI or came in via config, and that
distinction drives how to debug a missing-tool report.

Auth: admin-gated like every other Adam endpoints (read-only
data; non-sensitive — same gate as ``/api/employees``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from magi.channels.webui.api.departments import AdminGate
from magi.agent.tools.base import Tool
from magi.agent.tools.registry import get_tools_grouped

router = APIRouter(tags=["tools"])


class ToolOut(BaseModel):
    """One row in the dashboard's "Tools" pane.

    The full input schema is intentionally NOT returned — the
    dashboard only renders ``name`` / ``description-summary`` /
    a small property-listing indicator. The agent loop already
    has the full schemas (it calls the registry directly);
    shipping them to the browser is wasted payload.

    ``source`` distinguishes "builtin" (ships with MAGI) from
    "mcp" (loaded via ``mcp.json``). The dashboard renders
    these in two separate cards — when an operator can't find
    a tool, knowing which card it should be in cuts the
    debugging surface in half. ``"mcp"`` only appears if
    :func:`magi.agent.tools.registry.bootstrap_mcp_tools`
    has actually loaded something; on a fresh install this
    surface is naturally empty.

    ``prop_count`` is the number of properties in the JSON
    Schema's ``properties`` dict (for v0 most tools are zero or
    a handful). Non-zero tells the operator "this tool takes
    structured input".

    ``allowed_roles`` is the per-tool
    :attr:`magi.agent.tools.base.Tool.ALLOWED_ROLES`, sorted
    alphabetically so the dashboard renders a stable order.
    Empty list means the tool has no role restriction
    (``is_allowed_for_role(None) is True`` and the LLM sees it
    regardless of caller). Today every built-in declares a
    non-empty set; MCP tools come back unrestricted because
    ``MCPTool.is_allowed_for_role`` always returns True.
    """

    name: str
    description: str
    prop_count: int
    source: Literal["builtin", "mcp"] = "builtin"
    allowed_roles: list[str] = []    # sorted; [] = no role gate


class ToolListOut(BaseModel):
    """``items`` is sorted by name (stable across requests) so
    the dashboard can render the same order on every refresh."""

    items: list[ToolOut]
    total: int


def _summarize(description: str) -> str:
    """First 200 chars of the description, single line.

    Schema descriptions are multi-line on the source side; we
    collapse whitespace so the dashboard's one-line cell
    stays readable. ``...`` suffix on truncation so the
    operator can tell.
    """
    one_line = " ".join(description.split())
    if len(one_line) <= 200:
        return one_line
    return one_line[:197] + "..."


def _summarize_schema(schema: dict[str, Any]) -> int:
    """Count the JSON Schema's ``properties`` dict size.

    V0 doesn't expose full schemas (too noisy in a list view);
    just enough for the dashboard to show "takes 3 inputs".
    Returns 0 for any non-standard schema layout.
    """
    props = schema.get("properties")
    if isinstance(props, dict):
        return len(props)
    return 0


@router.get("/tools", response_model=ToolListOut)
def list_tools(_admin: AdminGate) -> ToolListOut:
    """Render the current tool registry as a flat list.

    No filtering, no pagination — v0 ships a handful of tools
    total (5 built-ins + a small MCP fan-out, if configured).
    The flat list mirrors
    :func:`magi.agent.tools.registry.get_tools_grouped`; if
    that ever grows past ~50 entries, surface
    ``?source=builtin|mcp`` and a paginated view here.

    ``caller_role=None`` is intentional — we want every
    tool visible to the dashboard (read-only audit view),
    regardless of who's currently logged in. The dashboard
    shows the registry truth; the agent loop still passes
    the operator's ``employee.role`` to
    ``get_tool_schemas(caller_role=...)`` for the LLM's
    menu at chat time.

    Builtins come back first in the response (the
    dashboard groups them under their own card); MCP
    tools follow. Within each group the items are
    lexicographically sorted by name so refreshes
    re-render the same layout.
    """
    built_in, mcp = get_tools_grouped(caller_role=None)
    items: list[ToolOut] = [
        _serialize_tool(tool, "builtin") for tool in built_in
    ] + [
        _serialize_tool(tool, "mcp") for tool in mcp
    ]
    # Stable ordering by name keeps the dashboard layout
    # deterministic across refreshes; ``registry`` returns
    # the built-in-first + MCP-appended order which is also
    # stable but harder to reason about in a diff. Sort
    # AFTER concatenation so MCP and built-in tools
    # intermix lexicographically (each card groups them
    # client-side by ``source``).
    items.sort(key=lambda t: t.name)
    return ToolListOut(items=items, total=len(items))


def _serialize_tool(
    tool: Tool, source: Literal["builtin", "mcp"],
) -> ToolOut:
    """Render one :class:`Tool` instance to a
    :class:`ToolOut` row. Pulled out of the route body
    so tests can poke a single tool without going through
    the registry.

    ``source`` is passed in by the caller — there's no
    way to introspect the tool to learn "did this come
    from a builtin or an MCP server" (both implement the
    Tool protocol via duck typing), so the route handler
    tells us which cache it pulled from."""
    schema = tool.to_anthropic_schema()
    return ToolOut(
        name=schema.get("name", ""),
        description=_summarize(schema.get("description", "") or ""),
        prop_count=_summarize_schema(schema.get("input_schema") or {}),
        source=source,
        # Sorted so the dashboard renders a stable,
        # human-readable order across reloads (frozensets
        # aren't stable-across-Python-versions by default).
        allowed_roles=sorted(tool.ALLOWED_ROLES),
    )
