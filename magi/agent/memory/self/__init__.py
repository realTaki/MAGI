"""Self memory — long-lived facts and ongoing work for the current context.

Stores the things the operator has told the EVE to
"remember" (company policies, contract deadlines,
ongoing projects, follow-ups). This is **MAGI's own
memory** — not a record of people. Person records
("Lily 在财务部") live in
:mod:`magi.agent.memory.contacts`.

The LLM manages the table through four tools
(:mod:`.tools`): add / update / complete / delete.
Writes are not automatic — the operator must say
"记住 X" or the LLM must judge the fact long-arc
enough to persist.

The system-prompt formatter
(:func:`.prompt.format_memory_block`) renders the
operator's important + ongoing rows as a Markdown
block appended to the LLM's system prompt. The block
is empty when there's nothing to remember; the agent
loop short-circuits to keep the prompt lean.

Layout:

  - :mod:`.models`  — :class:`MemoryEntry` ORM table
  - :mod:`.store`   — :class:`MemoryStore` CRUD
  - :mod:`.prompt`  — :func:`format_memory_block`
  - :mod:`.tools`   — the four LLM-callable tools
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
from magi.agent.memory.self.tools import (
    AddMemoryTool,
    CompleteMemoryTool,
    DeleteMemoryTool,
    UpdateMemoryTool,
)


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
    # tools
    "AddMemoryTool",
    "UpdateMemoryTool",
    "CompleteMemoryTool",
    "DeleteMemoryTool",
]
