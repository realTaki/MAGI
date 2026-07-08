"""LLM-callable memory tools.

The agent loop exposes these to the LLM through the
standard :mod:`magi.agent.tools.registry` mechanism. The
LLM decides when to call them based on operator
instructions ("记住 X" / "在跟进 Y" / "Lily 是谁来着").

Four separate tools rather than one ``memory`` tool
with a verb discriminator:

  - Anthropic's tool-use API lets the LLM call
    multiple tools in parallel within one turn. Splitting
    keeps each tool's schema small and unambiguous.
  - The error surface is per-tool: a malformed
    ``add_memory`` call returns ``is_error=True`` to
    that specific tool result, not to the whole turn.

Admin gate: same as the API — only ``admin`` and
``assigned`` employees can write to their own memory.
``employee`` and ``guest`` get ``is_error=True`` on every
write tool. Reads (no read tool yet — the system-prompt
block is the read path for v0) would carry the same
gate when added.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from magi.agent.db import Employee, open_session
from magi.agent.memory.models import (
    ALL_KINDS,
    ALL_SCOPES,
    KIND_PERSON,
    SCOPE_PRIMARY,
    SOURCE_EVE,
)
from magi.agent.memory.store import MemoryStore
from magi.agent.tools.base import Tool, ToolContext, ToolResult


logger = logging.getLogger("magi.agent.memory.tools")

_WRITE_ROLES = {"admin", "assigned"}


def _gate(ctx: ToolContext) -> str | None:
    """Return an error message if the caller's role
    can't write to memory, else ``None``.

    We re-resolve the role from the DB on every call
    rather than caching it on ``ctx`` because
    role flips (admin promotes the operator to
    ``assigned``) should take effect on the next
    LLM call, not after the next process restart.
    """
    try:
        emp_id = int(ctx.employee_id)
    except (TypeError, ValueError):
        return f"employee_id {ctx.employee_id!r} is not a valid id"
    with open_session() as db:
        emp = db.get(Employee, emp_id)
    if emp is None:
        return f"employee {emp_id!r} not found"
    if emp.role not in _WRITE_ROLES:
        return (
            f"role {emp.role!r} cannot write to memory; "
            "only admin and assigned may."
        )
    return None


def _err(msg: str) -> ToolResult:
    return ToolResult(content=msg, is_error=True)


def _ok(payload: dict) -> ToolResult:
    """Render a successful tool result as pretty JSON.

    ``json.dumps(..., indent=2)`` reads nicely in the
    LLM's tool-use transcript without the model
    having to mentally parse a single-line dump.
    Truncated to 4 KB to keep the next turn's input
    bounded.
    """
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(body) > 4 * 1024:
        body = body[: 4 * 1024] + "\n…(truncated)"
    return ToolResult(content=body, is_error=False)


class AddMemoryTool(Tool):
    """Persist a new memory row.

    The LLM calls this when the operator asks to
    remember something ("记住 X" / "记下 Y" / "the
    contract is due on 9/30" / "Lily is the finance
    lead"). The body is markdown; the LLM is
    responsible for the prose.

    Idempotency: for ``kind=person`` the LLM is
    expected to call this only for *new* people. The
    store's :meth:`MemoryStore.find_person` exposes
    the existing row so the LLM can decide to call
    :class:`UpdateMemoryTool` instead. (v0 doesn't
    enforce a unique constraint at the DB level
    because a person record may legitimately have
    multiple rows if the operator wants to track
    "Lily — finance" and "Lily — Q3 owner" as
    separate facts about the same person.)
    """

    name = "add_memory"
    description = (
        "Persist a new fact into the operator's long-term memory. "
        "Use when the operator says '记住 X' / '记下 Y' / '把 ... 记 "
        "录下来' — or when the LLM judges a fact worth remembering "
        "across conversations (company policy, contract deadline, "
        "ongoing project, a person the operator mentioned). "
        "kinds: 'important' (long-arc facts), 'ongoing' (work in "
        "flight, has a completion), 'person' (directory entry — "
        "requires person_employee_id). scope: 'primary' (the "
        "operator's own facts) or 'secondary' (someone else's "
        "directory entry — typically kind=person)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(ALL_KINDS),
                "description": "important | ongoing | person",
            },
            "subject": {
                "type": "string",
                "description": (
                    "Short title. <=200 chars. The bullet in the "
                    "system-prompt block renders this verbatim."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Full body. Markdown. <=8 KB. Repeating the "
                    "subject in the body is fine — the LLM often "
                    "re-structures the subject into the body "
                    "when it has more context."
                ),
            },
            "importance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": (
                    "1 (low) .. 5 (critical). 'important' rows "
                    "default to 4-5; 'ongoing' rows default to "
                    "2-3 so the operator can deprioritise."
                ),
            },
            "scope": {
                "type": "string",
                "enum": sorted(ALL_SCOPES),
                "description": (
                    "primary = operator's own memory. secondary = "
                    "someone else's directory entry."
                ),
            },
            "person_employee_id": {
                "type": "integer",
                "description": (
                    "Required when kind=person. The employees.id "
                    "of the person being described."
                ),
            },
        },
        "required": ["kind", "subject", "body"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)

        try:
            store = MemoryStore(ctx.state_dir)
            view = store.add(
                int(ctx.employee_id),
                kind=kwargs["kind"],
                subject=kwargs["subject"],
                body=kwargs["body"],
                scope=kwargs.get("scope", SCOPE_PRIMARY),
                person_employee_id=kwargs.get("person_employee_id"),
                importance=kwargs.get("importance", 3),
                source=SOURCE_EVE,
            )
        except (ValueError, KeyError) as e:
            return _err(f"add_memory failed: {e}")
        return _ok(view.to_dict())


class UpdateMemoryTool(Tool):
    """Patch an existing memory row by id.

    The LLM finds the id via the system-prompt block
    ("memory id 17 says …") or via the ``list_memory``
    read tool (TODO when added). Mutable fields only
    — ``kind`` and ``person_employee_id`` are
    intentionally not editable to keep the row's
    identity stable across edits.
    """

    name = "update_memory"
    description = (
        "Patch an existing memory row by id. Use when the operator "
        "says '更新 X' / '改成 ...' / 'the deadline is now 10/15' / "
        "'Lily now reports to Mark'. Mutable: subject, body, "
        "importance, scope. Immutable: kind, person_employee_id "
        "(delete + re-add if you really need to change those)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the row to patch (from add_memory result, or visible in the system-prompt block as 'memory id N: ...').",
            },
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5},
            "scope": {
                "type": "string",
                "enum": sorted(ALL_SCOPES),
            },
        },
        "required": ["memory_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)

        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return _err(f"memory_id must be int, got {type(memory_id).__name__}")
        try:
            store = MemoryStore(ctx.state_dir)
            view = store.update(
                memory_id,
                subject=kwargs.get("subject"),
                body=kwargs.get("body"),
                importance=kwargs.get("importance"),
                scope=kwargs.get("scope"),
            )
        except LookupError as e:
            return _err(str(e))
        except ValueError as e:
            return _err(f"update_memory failed: {e}")
        return _ok(view.to_dict())


class CompleteMemoryTool(Tool):
    """Mark an ``ongoing`` row as done.

    Sets ``completed_at`` to the current UTC. The row
    stays in the table for the audit trail but drops
    out of the system-prompt formatter.
    """

    name = "complete_memory"
    description = (
        "Mark an ongoing memory row as done. The row stays in the "
        "table for the audit trail but is no longer rendered in "
        "the system-prompt block. Use when the operator says "
        "'完成了' / '搞定了' / 'the project shipped'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the ongoing row to mark done.",
            },
        },
        "required": ["memory_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)

        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return _err(f"memory_id must be int, got {type(memory_id).__name__}")
        try:
            store = MemoryStore(ctx.state_dir)
            view = store.complete(memory_id)
        except LookupError as e:
            return _err(str(e))
        return _ok(view.to_dict())


class DeleteMemoryTool(Tool):
    """Remove a memory row.

    Idempotent — deleting a non-existent id is a
    successful no-op. The LLM can retry without
    seeing a false ``is_error``.
    """

    name = "delete_memory"
    description = (
        "Delete a memory row by id. Idempotent — deleting a "
        "non-existent id returns success. Use when the operator "
        "says '忘了 X' / '那条记错了删掉' / 'Lily no longer works "
        "here'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "id of the row to remove.",
            },
        },
        "required": ["memory_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        gate = _gate(ctx)
        if gate is not None:
            return _err(gate)

        memory_id = kwargs.get("memory_id")
        if not isinstance(memory_id, int):
            return _err(f"memory_id must be int, got {type(memory_id).__name__}")
        store = MemoryStore(ctx.state_dir)
        existed = store.delete(memory_id)
        return _ok({"memory_id": memory_id, "existed": existed})


__all__ = [
    "AddMemoryTool",
    "UpdateMemoryTool",
    "CompleteMemoryTool",
    "DeleteMemoryTool",
]