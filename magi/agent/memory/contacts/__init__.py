"""Contact directory — what the MAGI knows about people.

The BUS owns durable contact persistence. This package contains only prompt
formatting for agent context.

Tool classes live in :mod:`magi.tools.memory_contacts` — they
were moved out of this package to break a dependency cycle
(agent → memory → tools → memory).
"""

from __future__ import annotations

from magi.bus.contracts.contact import (
    ContactView,
    NoteView,
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
)
from magi.agent.memory.contacts.prompt import (
    format_contact_block,
    format_daily_note_block,
)


__all__ = [
    "SOURCE_EVE",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
    "ContactView",
    "NoteView",
    "format_contact_block",
    "format_daily_note_block",
]
