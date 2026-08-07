
"""LLM-callable action-item tools.

Three surfaces pinned:

  - :class:`AddActionItemTool` — record a new action item for
    the calling operator (umbrella term: "todo", "task",
    "记一下", "待办" — all map here) (``kind='llm_action_item_<id>'``,
    ``source='llm'``, ``uid=ctx.uid``).
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
    contact privacy: a tool call from operator A
    never sees operator B's rows, even if the LLM asks
    for an id it doesn't own — the row is missing
    rather than shared.

Scope (per-contact, role-gated):

  - The assigned user (``'assigned'``) and any operator
    (``admin=True``) can use these tools for their own
    action items only. The ``guest`` role doesn't see
    the tools in their menu: the registry's
    :func:`get_tools(caller_role=...)` filter (see
    ``magi/tools/registry.py``) strips them out
    before the LLM sees the schema.
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

from magi.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
)

logger = logging.getLogger("magi.tools.tasks.action_item")

# Same gate as the WebUI API and as ``ScheduleTaskTool``:
# only ``admin`` and ``assigned`` operators may operate
# on their own action items. ``contact`` and ``guest``
# have no MAGI-node session and aren't expected to chat
# Role gating is centralised in :meth:`Tool.gate`; tools
# declare their role whitelist via :attr:`Tool.ALLOWED_ROLES`
# on the class — no per-module `_ALLOWED_ROLES` / `_gate()`
# helpers.

# Stable kind prefix for LLM-driven action items. Each row
# gets a unique per-row suffix (``_<8-hex>``) so multiple
# open action items per operator don't collide with the
# partial unique index ``ux_action_items_open_per_kind``
# (which enforces one OPEN row per ``(uid, kind)``
# for stable system kinds like ``llm_credentials_missing``).
# ``list_action_item`` filters by
# ``kind LIKE 'llm_action_item_%'``.
_LLM_ACTION_ITEM_KIND_PREFIX = "llm_action_item"


def _new_llm_action_item_kind() -> str:
    return f"{_LLM_ACTION_ITEM_KIND_PREFIX}_{uuid.uuid4().hex[:8]}"


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
    :func:`magi.channels.api.action_items._iso` —
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
        "('normal' default / 'high'), due_date "
        "(optional ISO date like '2026-07-30'), "
        "target_url (optional in-app link). Each call creates one "
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
            "due_date": {
                "type": "string",
                "description": (
                    "Optional deadline in ISO date "
                    "format ('YYYY-MM-DD' or "
                    "'YYYY-MM-DDTHH:MM'). Null / "
                    "omitted means 'no deadline'. "
                    "The dashboard shows it alongside "
                    "the title; past-due items remain "
                    "visible — the operator dismisses "
                    "them manually."
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

        due_date = None
        raw_due = kwargs.get("due_date")
        if raw_due is not None and str(raw_due).strip():
            raw = str(raw_due).strip()
            # Accept YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS] as
            # lenient date parsing. We don't validate
            # calendar correctness — the ORM column is
            # nullable; a malformed date that parses to
            # NaN will be silently set to None.
            from datetime import datetime as dt
            try:
                # Try ISO datetime first
                due_date = dt.fromisoformat(raw)
            except ValueError:
                try:
                    # Fallback: date-only
                    due_date = dt.strptime(raw, "%Y-%m-%d")
                except ValueError:
                    return _err(
                        f"due_date must be a valid date "
                        f"(YYYY-MM-DD), got {raw!r}"
                    )

        item = ctx.bus.action_items_book.create_llm(
            uid=int(ctx.uid), kind=_new_llm_action_item_kind(), title=title,
            description=description, target_url=target_url, priority=priority, due_date=due_date,
        )
        logger.info(
            "add_action_item: item %s created for contact=%s title=%r",
            item.id, ctx.uid, title,
        )
        return _ok({"created": item.to_dict()})


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
                    "existence (strict per-contact "
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
        raw_id = kwargs.get("item_id")
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            return _err(f"item_id must be an integer, got {raw_id!r}")
        note = kwargs.get("note")
        if note is not None and len(note) > 500:
            return _err(f"note is too long ({len(note)} > 500)")

        ct_id = int(ctx.uid)
        row = ctx.bus.action_items_book.complete_for_owner(
            action_item_id=item_id, owner_uid=ct_id, note=note,
        )
        if row is None:
            return _err(f"action item {item_id} not found or not owned by the calling operator")
        logger.info("complete_action_item: item %s completed by %s", item_id, ct_id)
        return _ok({"item": row.to_dict()})


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
        "per-contact: only rows owned by the caller "
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
        ct_id = int(ctx.uid)
        include_completed = bool(kwargs.get("include_completed"))

        rows = ctx.bus.action_items_book.list_llm_for_owner(
            owner_uid=ct_id, include_completed=include_completed,
        )
        return _ok({
            "items": [row.to_dict() for row in rows],
            "total": len(rows),
        })


__all__ = [
    "AddActionItemTool",
    "CompleteActionItemTool",
    "ListActionItemTool",
]
