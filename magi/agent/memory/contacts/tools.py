"""LLM-callable contact tools.

The contact directory is LLM-managed, not auto-managed.
The LLM hears "Lily 在财务部" and calls
``add_contact`` to record it; later the operator says
"对了 Lily 现在不负责这块了" and the LLM calls
``update_contact`` to fix it.

Four tools mirror the magi-memory shape:

  - :class:`AddContactTool` — upsert by (owner, person).
    Idempotent: re-call with the same ``person_id``
    patches the existing row.
  - :class:`UpdateContactTool` — patch ``notes`` /
    ``role`` by contact id.
  - :class:`DeleteContactTool` — remove by id.
  - :class:`SearchContactsTool` — read path. Returns
    contacts whose ``notes`` match a substring
    (case-insensitive). The LLM uses this when the
    operator says "记得 Mark 在哪吗" — the LLM
    searches and finds them.

Admin gate: same as the API and the magi-memory
tools — only ``admin`` and ``assigned`` may write
to their own directory. ``employee`` and ``guest``
get ``is_error=True``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select

from magi.agent.db import Employee, open_session
from magi.agent.memory.contacts.models import ContactEntry
from magi.agent.memory.contacts.store import ContactStore
from magi.agent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    caller_role_denied_reason,
)


logger = logging.getLogger("magi.agent.memory.contacts.tools")

_WRITE_ROLES = frozenset({"admin", "assigned"})


def _gate(ctx: ToolContext) -> str | None:
    """Thin wrapper around
    :func:`magi.agent.tools.base.caller_role_denied_reason`
    — the canonical in-run gate lives there so contacts,
    memory, and action-item tools share one check. The
    per-tool wrapper exists only so the call sites read
    ``denied = _gate(ctx)`` without referring to
    ``self.ALLOWED_ROLES``."""
    return caller_role_denied_reason(ctx, _WRITE_ROLES)


def _err(msg: str) -> ToolResult:
    return ToolResult(content=msg, is_error=True)


def _ok(payload: Any) -> ToolResult:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(body) > 4 * 1024:
        body = body[: 4 * 1024] + "\n…(truncated)"
    return ToolResult(content=body, is_error=False)


class AddContactTool(Tool):
    """Upsert a contact record by person_id.

    Idempotent: if a row already exists for the
    (owner, person) pair, the existing row is
    patched with the new notes / role. This is the
    common path — the LLM hears the same person
    mentioned in multiple turns and we want one
    cumulative record, not a journal.
    """

    name = "add_contact"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Record what the MAGI knows about a person. "
        "Idempotent: re-call with the same person_id "
        "patches the existing row rather than creating "
        "a duplicate. Use when the operator says '记住 "
        "Lily 在财务部' / 'Mark 是我们 CTO' / '记得 Bob "
        "prefer Slack over email'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "person_id": {
                "type": "integer",
                "description": "employees.id of the person being described.",
            },
            "notes": {
                "type": "string",
                "description": "Free-form markdown. <=8 KB. The current chatter's prompt renders this verbatim.",
            },
            "role": {
                "type": "string",
                "description": (
                    "Optional snapshot of the person's role at "
                    "the time of recording (e.g. 'finance lead', "
                    "'CTO'). Distinct from the live role on the "
                    "employees row, which can change over time."
                ),
            },
        },
        "required": ["person_id", "notes"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)
        person_id = kwargs.get("person_id")
        if not isinstance(person_id, int):
            return _err(
                f"person_id must be int, got {type(person_id).__name__}"
            )
        try:
            store = ContactStore(ctx.state_dir)
            view = store.upsert(
                int(ctx.uid),
                person_id,
                notes=kwargs["notes"],
                role=kwargs.get("role"),
            )
        except ValueError as e:
            return _err(f"add_contact failed: {e}")
        return _ok(view.to_dict())


class UpdateContactTool(Tool):
    """Patch an existing contact record by id."""

    name = "update_contact"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Patch an existing contact record by id. Use when "
        "the operator says '对了 Lily 现在不负责这块了' / "
        "'Mark 的 role 改了'. Mutable: notes, role. "
        "Immutable: person_id, owner_id (delete + re-add if "
        "you really need to change those)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "integer",
                "description": "id of the contact row (from add_contact result).",
            },
            "notes": {"type": "string"},
            "role": {"type": "string"},
        },
        "required": ["contact_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)

        contact_id = kwargs.get("contact_id")
        if not isinstance(contact_id, int):
            return _err(
                f"contact_id must be int, got {type(contact_id).__name__}"
            )
        try:
            store = ContactStore(ctx.state_dir)
            view = store.update(
                contact_id,
                notes=kwargs.get("notes"),
                role=kwargs.get("role"),
            )
        except LookupError as e:
            return _err(str(e))
        return _ok(view.to_dict())


class DeleteContactTool(Tool):
    """Remove a contact record by id.

    Idempotent — deleting a non-existent id is a
    successful no-op.
    """

    name = "delete_contact"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Delete a contact record by id. Idempotent — "
        "deleting a non-existent id returns success. "
        "Use when the operator says '忘了 Lily 吧' / "
        "'删掉那条 Mark 的记录'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "integer",
                "description": "id of the contact row to remove.",
            },
        },
        "required": ["contact_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)

        contact_id = kwargs.get("contact_id")
        if not isinstance(contact_id, int):
            return _err(
                f"contact_id must be int, got {type(contact_id).__name__}"
            )
        store = ContactStore(ctx.state_dir)
        existed = store.delete(contact_id)
        return _ok({"contact_id": contact_id, "existed": existed})


class SearchContactsTool(Tool):
    """Read path: search the operator's contact
    directory.

    Returns all of the operator's contacts whose
    ``notes`` contain the query substring (case-
    insensitive). The LLM uses this when the
    operator says "记得 Mark 在哪吗" / "谁在负责
    Q3 报销" — the LLM searches and answers from
    the result.
    """

    name = "search_contacts"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Search the contact directory. Returns all of the "
        "operator's contacts whose notes contain the query "
        "substring (case-insensitive). Use when the operator "
        "says '记得 Mark 在哪吗' / '谁在负责 Q3 报销' / "
        "'Lily 干啥的来着'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring to search for in contact notes. Case-insensitive.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
            },
        },
        "required": ["query"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        # Read path — no role gate. Any signed-in
        # employee can search (the WebUI exposes this
        # to admin + assigned anyway).
        query = (kwargs.get("query") or "").strip()
        if not query:
            return _err("query is required")
        limit = int(kwargs.get("limit") or 20)

        pattern = f"%{query}%"
        with open_session() as db:
            rows = db.execute(
                select(ContactEntry)
                .where(
                    ContactEntry.owner_id == int(ctx.uid),
                    ContactEntry.notes.ilike(pattern),
                )
                .order_by(ContactEntry.last_seen_at.desc())
                .limit(limit)
            ).scalars().all()
        return _ok({
            "query": query,
            "matches": [
                {
                    "id": r.id,
                    "person_id": r.person_id,
                    "role": r.role,
                    "notes": r.notes,
                    "last_seen_at": r.last_seen_at.isoformat()
                        .replace("+00:00", "Z"),
                }
                for r in rows
            ],
        })


__all__ = [
    "AddContactTool",
    "UpdateContactTool",
    "DeleteContactTool",
    "SearchContactsTool",
]