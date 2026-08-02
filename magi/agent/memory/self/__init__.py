"""Self memory — long-lived facts and ongoing work for the current context.

Stores durable facts and ongoing work for the current
runtime context (company policies, contract deadlines,
ongoing projects, follow-ups). It is **self memory** —
not a record of people. Person records
("Lily 在财务部") live in
:mod:`magi.agent.memory.contacts`.

The LLM manages the table through four tools
(in :mod:`magi.tools.memory_self`): add / update / complete / delete.

Layout:

  - :mod:`.models`  — :class:`MemoryEntry` ORM table
  - :mod:`.store`   — :class:`MemoryStore` CRUD
  - :mod:`.prompt`  — :func:`format_memory_block`
"""

from __future__ import annotations

from magi.agent.memory.self.models import (
    ALL_KINDS,
    KIND_IMPORTANT,
    KIND_ONGOING,
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
    MemoryEntry,
)
from magi.agent.memory.self.prompt import format_memory_block
from magi.agent.memory.self.store import MemoryStore, MemoryView


__all__ = [
    # enums
    "ALL_KINDS",
    "KIND_IMPORTANT",
    "KIND_ONGOING",
    "SOURCE_EVE",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
    # data
    "MemoryEntry",
    "MemoryStore",
    "MemoryView",
    # formatter
    "format_memory_block",
]
