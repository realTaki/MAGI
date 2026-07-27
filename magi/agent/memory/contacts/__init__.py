"""Contact directory — what the MAGI knows about people.

The unified ``contacts`` table (see :mod:`magi.agent.db.models_contact`)
holds every person row. Each ``Contact`` carries a ``notes``
field (LLM-managed free-form markdown) and a ``source`` field.

Layout:

  - :mod:`.store`   — :class:`ContactStore` CRUD
  - :mod:`.prompt`  — :func:`format_contact_block`
  - :mod:`.tools`   — LLM-callable tools
"""

from __future__ import annotations

from magi.agent.db.models_contact import (
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
)
from magi.agent.memory.contacts.prompt import format_contact_block
from magi.agent.memory.contacts.store import ContactStore, ContactView
from magi.agent.memory.contacts.tools import (
    AddContactNoteTool,
    AddContactTool,
    DeleteContactNoteTool,
    SearchContactsTool,
    UpdateContactNoteTool,
)


__all__ = [
    "SOURCE_EVE",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
    "ContactStore",
    "ContactView",
    "format_contact_block",
    "AddContactTool",
    "AddContactNoteTool",
    "UpdateContactNoteTool",
    "DeleteContactNoteTool",
    "SearchContactsTool",
]
