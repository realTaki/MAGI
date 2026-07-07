"""Tools — list every tool the LLM can invoke.

End-user-facing read-only view of
:meth:`magi.runtime.tools.registry.get_tool_schemas`. Useful for
the operator to verify what their MAGI install can actually
do — ``mcp.json`` loaded the right servers, no LLM tool wedge
broke, etc.

The endpoint returns the JSON-Schema (Anthropic-shaped) for
every registered tool — built-ins + MCP-loaded. ``description``
is the first 200 chars of the schema description so the
dashboard can render a one-line glance without re-fetching.

Auth: admin-gated like every other Adam endpoints (read-only
data; non-sensitive — same gate as ``/api/employees``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from magi.channels.webui.api.departments import AdminGate
from magi.runtime.tools.registry import get_tool_schemas

router = APIRouter(tags=["tools"])


class ToolOut(BaseModel):
    """One row in the dashboard's "Tools" pane.

    The full input schema is intentionally NOT returned — the
    dashboard only renders ``name`` / ``description-summary`` /
    a small property-listing indicator. The agent loop already
    has the full schemas (it calls the registry directly);
    shipping them to the browser is wasted payload.

    ``prop_count`` is the number of properties in the JSON
    Schema's ``properties`` dict (for v0 most tools are zero or
    a handful). Non-zero tells the operator "this tool takes
    structured input".
    """

    name: str
    description: str
    prop_count: int


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
    The flat list mirrors ``registry.get_tool_schemas()`` byte
    for byte; if that ever grows past ~50 entries, surface
    ``?source=builtin|mcp`` and a paginated view here.
    """
    schemas = get_tool_schemas()
    items = [
        ToolOut(
            name=raw.get("name", ""),
            description=_summarize(raw.get("description", "") or ""),
            prop_count=_summarize_schema(raw.get("input_schema") or {}),
        )
        for raw in schemas
    ]
    # Stable ordering by name keeps the dashboard layout
    # deterministic across refreshes; ``registry`` returns the
    # built-in-first + MCP-appended order which is also stable
    # but harder to reason about in a diff.
    items.sort(key=lambda t: t.name)
    return ToolListOut(items=items, total=len(items))
