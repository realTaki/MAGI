"""Tools — list every tool the LLM can invoke.

Reads from the ``tools`` database table (seeded by the tool worker on
startup).  Reflects the same data the agent loop uses to build the LLM's
tool menu, so the operator sees exactly what the model can call.

Auth: admin-gated like every other Adam endpoints (read-only
data; non-sensitive — same gate as ``/api/contacts``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from magi.channels.webui.api.auth_gates import AdminGate
from magi.db.engine import require_state_dir
from magi.db.tool_schemas import get_tools_grouped as _get_tools_grouped, get_tool_schemas as _get_tool_schemas

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
    :func:`magi.tools.registry.bootstrap_mcp_tools`
    has actually loaded something; on a fresh install this
    surface is naturally empty.

    ``prop_count`` is the number of properties in the JSON
    Schema's ``properties`` dict (for v0 most tools are zero or
    a handful). Non-zero tells the operator "this tool takes
    structured input".

    ``allowed_roles`` is the per-tool
    :attr:`magi.tools.base.Tool.ALLOWED_ROLES`, sorted
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
    server: str | None = None        # MCP server name this tool came from


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


def _build_tool_out(
    schema: dict[str, Any],
    source: Literal["builtin", "mcp"],
    allowed_roles: list[str] | None = None,
) -> ToolOut:
    """Build a :class:`ToolOut` from a DB schema row."""
    name = str(schema.get("name", ""))
    server: str | None = None
    if source == "mcp" and "__" in name:
        server = name.split("__", 1)[0]
    return ToolOut(
        name=name,
        description=_summarize(str(schema.get("description", "") or "")),
        prop_count=_summarize_schema(schema.get("input_schema") or {}),
        source=source,
        allowed_roles=sorted(allowed_roles) if allowed_roles else [],
        server=server,
    )


@router.get("/tools", response_model=ToolListOut)
def list_tools(_admin: AdminGate) -> ToolListOut:
    """Render the current tool registry as a flat list, read from the DB."""
    state_dir = require_state_dir()
    built_in_schemas, mcp_schemas = _get_tools_grouped(state_dir)
    items: list[ToolOut] = [
        _build_tool_out(schema, "builtin") for schema in built_in_schemas
    ] + [
        _build_tool_out(schema, "mcp") for schema in mcp_schemas
    ]
    items.sort(key=lambda t: t.name)
    return ToolListOut(items=items, total=len(items))
