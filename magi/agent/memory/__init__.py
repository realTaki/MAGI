"""MAGI long-term memory — per-assigned-employee facts.

What lives here:

  - **Important** — long-arc facts the LLM should not
    forget (company policy, contract deadline,
    "never do X" rules).
  - **Ongoing** — work in flight; the LLM tracks
    progress and marks the row done via
    :class:`CompleteMemoryTool`.
  - **People** — directory entries. "Lily is in
    finance, telegram_id=9001". The LLM uses these to
    recognise names when the operator mentions them
    or asks "send Lily a message".

Per-MAGI scope: each MAGI has its own ``memory_entries``
rows, keyed by ``employee_id`` (the assigned employee
on this MAGI). On a single-instance setup that's one
employee per MAGI; the model generalises to multi-
tenant by adding a ``tenant_id`` later.

The LLM manages the table through four tools
(:mod:`.tools`) — not automatically on every chat
turn. The operator must say "记住 X" (or the LLM
must judge the fact long-arc enough to persist).

Layout:

  - :mod:`.models`  — :class:`MemoryEntry` ORM table
  - :mod:`.store`   — :class:`MemoryStore` CRUD
  - :mod:`.prompt`  — :func:`format_memory_block`
                      (system-prompt formatter)
  - :mod:`.tools`   — the four LLM-callable tools
"""

from __future__ import annotations

from magi.agent.memory.models import (
    ALL_KINDS,
    ALL_SCOPES,
    KIND_IMPORTANT,
    KIND_ONGOING,
    KIND_PERSON,
    SCOPE_PRIMARY,
    SCOPE_SECONDARY,
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
    MemoryEntry,
)
from magi.agent.memory.prompt import format_memory_block
from magi.agent.memory.store import MemoryStore, MemoryView
from magi.agent.memory.tools import (
    AddMemoryTool,
    CompleteMemoryTool,
    DeleteMemoryTool,
    UpdateMemoryTool,
)


__all__ = [
    # enums
    "ALL_KINDS",
    "ALL_SCOPES",
    "KIND_IMPORTANT",
    "KIND_ONGOING",
    "KIND_PERSON",
    "SCOPE_PRIMARY",
    "SCOPE_SECONDARY",
    "SOURCE_EVE",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
    # data
    "MemoryEntry",
    "MemoryStore",
    "MemoryView",
    # formatter
    "format_memory_block",
    # tools
    "AddMemoryTool",
    "UpdateMemoryTool",
    "CompleteMemoryTool",
    "DeleteMemoryTool",
]