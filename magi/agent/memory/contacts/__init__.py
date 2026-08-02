"""Contact directory — what the MAGI knows about people.

The unified ``contacts`` table (see :mod:`magi.db.models_contact`)
holds every person row. Each ``Contact`` carries a ``notes``
field (LLM-managed free-form markdown) and a ``source`` field.

Tool classes live in :mod:`magi.tools.memory_contacts` — they
were moved out of this package to break a dependency cycle
(agent → memory → tools → memory).
"""

from __future__ import annotations

from magi.db.models_contact import (
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
)
from magi.agent.memory.contacts.prompt import (
    format_contact_block,
    format_daily_note_block,
)
from magi.agent.memory.contacts.store import ContactStore, ContactView


__all__ = [
    "SOURCE_EVE",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
    "ContactStore",
    "ContactView",
    "format_contact_block",
    "format_daily_note_block",
]
