"""LLM-callable action-item tools.

Three surfaces pinned:

  - :class:`AddActionItemTool` — record a new action item for
    the calling operator (umbrella term: "todo", "task",
    "记一下", "待办" — all map here) (``kind='llm_action_item_<id>'``,
    ``source='llm'``, ``employee_id=ctx.employee_id``).
    Re-calling with the same title creates a *new*
    row — the operator may want two parallel action
    items with similar titles; we don't guess
    duplicates from a free-text title.
  - :class:`CompleteActionItemTool` — close an existing open
    action item by id. Idempotent; re-calling on an
    already-completed row returns the existing row
    (same convention as ``/api/action_items/{id}/complete``).
  - :class:`ListActionItemTool` — return this operator's
    *own* open (or all) action items. Strict per-
    employee privacy: a tool call from operator A
    never sees operator B's rows, even if the LLM asks
    for an id it doesn't own — the row is missing
    rather than shared.

Scope (per-employee, role-gated):

  - Admin (``'admin'``) and assigned (``'assigned'``)
    operators can use these tools for their own action
    items only. Other roles (``'employee'``, ``'guest'``)
    don't even see the tools in their menu: the
    registry's :func:`get_tools(caller_role=...)`
    filter (see ``magi/agent/tools/registry.py``)
    strips them out before the LLM sees the schema.
  - Each tool also re-checks the caller's role inside
    ``run`` (belt-and-suspenders) — a future caller that
    bypasses ``get_tools`` (or calls the tool class
    directly in a test without role context) still
    fails closed with ``is_error=True``.

Why these three and not ``update_action_item``: the LLM
mostly either *records* (Add) or *closes* (Complete) —
edits to a non-completed action item are usually "I
got the title wrong, mark it done and re-add" rather
than "fix this specific field". If the LLM starts
needing field-by-field edit, add ``update_action_item``
later.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from magi.agent.db import ActionItem, open_session
from magi.agent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    caller_role_denied_reason,
)

logger = logging.getLogger("magi.agent.tools.action_item")

# Same gate as the WebUI API and as ``ScheduleTaskTool``:
# only ``admin`` and ``assigned`` operators may operate
# on their own action items. ``employee`` and ``guest``
# have no MAGI-node session and aren't expected to chat
# via the dashboard.
_ALLOWED_ROLES = frozenset({"admin", "assigned"})

# Stable kind prefix for LLM-driven action items. Each row
# gets a unique per-row suffix (``_<8-hex>``) so multiple
# open action items per operator don't collide with the
# partial unique index ``ux_action_items_open_per_kind``
# (which enforces one OPEN row per ``(employee_id, kind)``
# for stable system kinds like ``llm_credentials_missing``).
# ``list_action_item`` filters by
# ``kind LIKE 'llm_action_item_%'``.
_LLM_ACTION_ITEM_KIND_PREFIX = "llm_action_item"


def _new_llm_action_item_kind() -> str:
    return f"{_LLM_ACTION_ITEM_KIND_PREFIX}_{uuid.uuid4().hex[:8]}"


def _gate(ctx: ToolContext) -> str | None:
    """Thin wrapper around
    :func:`magi.agent.tools.base.caller_role_denied_reason`
    — kept as a free function so the call sites in
    each tool class read the same (``denied = _gate(ctx)``)
    without needing ``self.ALLOWED_ROLES`` everywhere.

    Single source of truth for the in-run gate lives in
    :func:`caller_role_denied_reason` so a future change
    to the check (e.g. adding a rate-limit) lives in one
    place.
    """
    return caller_role_denied_reason(ctx, _ALLOWED_ROLES)


def _err(msg: str) -> ToolResult:
    return ToolResult(content=msg, is_error=True)


def _ok(payload: Any) -> ToolResult:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    # 8 KB matches the LLM-side truncation budget in
    # ``ToolResult`` (``base.ToolResult`` docstring) —
    # a chat turn shouldn't return a multi-KB action-item
    # list when the operator can just look at the dashboard.
    if len(body) > 8 * 1024:
        body = body[: 8 * 1024] + "\n…(truncated)"
    return ToolResult(content=body, is_error=False)


def _iso(dt: datetime | None) -> str | None:
    """ISO-8601 UTC string. Mirrors
    :func:`magi.channels.webui.api.action_items._iso` —
    duplicated here to avoid pulling the WebUI router
    import graph into the agent loop's tool path
    (agent tools run without an HTTP request).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # ``ActionItem`` columns are naive UTC by way of
        # ``utcnow_naive()`` in the model.
        return dt.isoformat() + "Z"
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _serialize(item: ActionItem) -> dict[str, Any]:
    """JSON-friendly view of a row. Matches the shape
    ``/api/action_items`` returns (Pydantic-wise) so an
    operator looking at the dashboard sees the same row
    the LLM can see."""
    return {
        "id": item.id,
        "employee_id": item.employee_id,
        "kind": item.kind,
        "title": item.title,
        "description": item.description,
        "target_url": item.target_url,
        "priority": item.priority,
        "source": item.source,
        "created_at": _iso(item.created_at) or "",
        "completed_at": _iso(item.completed_at),
        "dismissed": item.dismissed,
    }


# -- AddActionItemTool ------------------------------------------------------------


class AddActionItemTool(Tool):
    """Record a new action item for the calling operator."""

    name = "add_action_item"
    description = (
        "Add an action item for the operator (visible in the "
        "dashboard's Action Items pane). Use when the "
        "operator says '帮我记一下 X' / 'todo ...' / "
        "'记得下周要 Y'. Returns the created row's id. "
        "Inputs: title (required, ≤200 chars), "
        "description (optional, ≤1000 chars), priority "
        "('normal' default / 'high'), target_url "
        "(optional in-app link). Each call creates one "
        "row; close with complete_action_item."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "What to do, ≤200 chars. The "
                    "operator-visible label."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Optional detail, ≤1000 chars. "
                    "Surfaces under the title in the "
                    "dashboard."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high"],
                "default": "normal",
                "description": (
                    "'high' sorts above 'normal' in the "
                    "operator's dashboard list. Use "
                    "sparingly — the dashboard doesn't "
                    "have a colour differentiation yet, "
                    "it's just an ordering key."
                ),
            },
            "target_url": {
                "type": "string",
                "description": (
                    "Optional in-app path ('/dashboard?"
                    "tab=...') for the action item's "
                    "'go to' button. v0 only supports "
                    "relative paths; absolute URLs are "
                    "ignored at render time."
                ),
            },
        },
        "required": ["title"],
    }

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        denied = _gate(ctx)
        if denied is not None:
            return _err(denied)
        title = (kwargs.get("title") or "").strip()
        if not title:
            return _err("title is required and must be non-empty")
        if len(title) > 200:
            return _err(f"title is too long ({len(title)} > 200)")
        description = kwargs.get("description")
        if description is not None and len(description) > 1000:
            return _err(
                f"description is too long ({len(description)} > 1000)"
            )
        priority = kwargs.get("priority") or "normal"
        if priority not in ("normal", "high"):
            return _err(
                f"priority must be 'normal' or 'high', got {priority!r}"
            )
        target_url = kwargs.get("target_url")
        if target_url is not None and len(target_url) > 500:
            return _err(
                f"target_url is too long ({len(target_url)} > 500)"
            )

        with open_session() as db:
            item = ActionItem(
                employee_id=int(ctx.employee_id),
                # Per-row unique kind so multiple open
                # action items per operator don't
                # collide with the partial unique index
                # ``ux_action_items_open_per_kind``. See
                # ``_new_llm_action_item_kind`` for the
                # format.
                kind=_new_llm_action_item_kind(),
                title=title,
                description=description,
                target_url=target_url,
                priority=priority,
                source="llm",
            )
            db.add(item)
            db.commit()
            db.refresh(item)
        logger.info(
            "add_action_item: item %s created for employee=%s title=%r",
            item.id, ctx.employee_id, title,
        )
        return _ok({"created": _serialize(item)})


# -- CompleteActionItemTool -------------------------------------------------------


class CompleteActionItemTool(Tool):
    """Close an existing open action item by id."""

    name = "complete_action_item"
    description = (
        "Mark one of the calling operator's action items "
        "complete. Idempotent: re-calling on an "
        "already-completed row returns the same "
        "state. Use when the operator says '做完 "
        "X 了' / 'close todo id=N' / '那条可以收 "
        "起来了'. Inputs: item_id (the action "
        "item's id; obtain it via list_action_item), "
        "note (optional ≤500 chars)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "integer",
                "description": (
                    "The action item's id. Only rows "
                    "owned by the calling operator "
                    "are completable — passing another "
                    "operator's id returns "
                    "is_error=True without leaking "
                    "existence (strict per-employee "
                    "privacy)."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional completion note (≤500 "
                    "chars). Surfaced in the "
                    "dashboard's 'recently completed' "
                    "list."
                ),
            },
        },
        "required": ["item_id"],
    }

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        denied = _gate(ctx)
        if denied is not None:
            return _err(denied)
        raw_id = kwargs.get("item_id")
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            return _err(f"item_id must be an integer, got {raw_id!r}")
        note = kwargs.get("note")
        if note is not None and len(note) > 500:
            return _err(f"note is too long ({len(note)} > 500)")

        emp_id = int(ctx.employee_id)
        with open_session() as db:
            row = db.get(ActionItem, item_id)
            if row is None:
                # Don't leak whether the id exists at
                # all — a 404 vs. an "owned by someone
                # else" 403 distinction is enough info
                # for an LLM to enumerate other
                # operators' action items.
                return _err(
                    f"action item {item_id} not found or not "
                    f"owned by the calling operator"
                )
            if row.employee_id != emp_id:
                logger.warning(
                    "complete_action_item denied: emp=%s tried to "
                    "complete item %s owned by %s",
                    emp_id, item_id, row.employee_id,
                )
                return _err(
                    f"action item {item_id} not found or "
                    f"not owned by the calling operator"
                )
            if row.completed_at is None:
                row.completed_at = datetime.now(timezone.utc).replace(
                    tzinfo=None
                )
                row.completed_by_employee_id = emp_id
                if note is not None:
                    row.completion_note = note
                db.commit()
                db.refresh(row)
                logger.info(
                    "complete_action_item: item %s completed by %s",
                    item_id, emp_id,
                )
            # else: idempotent — return the existing
            # row unchanged.
            return _ok({"item": _serialize(row)})


# -- ListActionItemTool -----------------------------------------------------------


class ListActionItemTool(Tool):
    """Return the calling operator's own action items."""

    name = "list_action_item"
    description = (
        "List the calling operator's action items. "
        "Use when the operator says '我还有哪些 "
        "todo' / '列出待办' / 'what's still open?' "
        "Inputs: include_completed (bool, default "
        "false — open action items only). Strict "
        "per-employee: only rows owned by the caller "
        "are returned. The operator's "
        "``llm_credentials_missing`` system row also "
        "appears here so the operator can see "
        "everything they own in one place."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "include_completed": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, include items already "
                    "completed or dismissed in the last "
                    "7 days (matches the dashboard's "
                    "default mix)."
                ),
            },
        },
    }

    ALLOWED_ROLES = frozenset({"admin", "assigned"})

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        denied = _gate(ctx)
        if denied is not None:
            return _err(denied)

        emp_id = int(ctx.employee_id)
        include_completed = bool(kwargs.get("include_completed"))

        with open_session() as db:
            # ``kind`` filter restricts to LLM-driven
            # action items — operators can have system-
            # seeded ``llm_credentials_missing`` rows
            # too, and those surface on the dashboard
            # but not via this tool (they're managed by
            # the onboarding flow, not the LLM). Per-row
            # unique suffix matches the prefix (see
            # ``AddActionItemTool``).
            stmt = select(ActionItem).where(
                ActionItem.employee_id == emp_id,
                ActionItem.kind.like(f"{_LLM_ACTION_ITEM_KIND_PREFIX}_%"),
            )
            if not include_completed:
                stmt = stmt.where(
                    ActionItem.completed_at.is_(None),
                    ActionItem.dismissed.is_(False),
                )
            stmt = stmt.order_by(
                ActionItem.completed_at.is_(None).desc(),
                ActionItem.priority.desc(),
                ActionItem.created_at.desc(),
            )
            rows = list(db.scalars(stmt).all())
        return _ok({
            "items": [_serialize(r) for r in rows],
            "total": len(rows),
        })


__all__ = [
    "AddActionItemTool",
    "CompleteActionItemTool",
    "ListActionItemTool",
]
