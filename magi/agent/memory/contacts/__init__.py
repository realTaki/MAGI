"""Contact directory — what the MAGI knows about people.

Each MAGI keeps a per-person record of facts it has
learned about each employee in the company. The
directory is LLM-managed: ``add_contact`` /
``update_contact`` / ``delete_contact`` /
``search_contacts`` are the LLM-callable tools;
the WebUI may add operator-driven tools later.

**The current chatter's contact is rendered into the
system prompt**; the rest of the directory is loaded
on demand via ``search_contacts``. This keeps the
prompt block small (one contact, ~1 KB) regardless of
how many people the MAGI knows about.

Layout:

  - :mod:`.models`  — :class:`ContactEntry` ORM table
                       (one row per (owner, person))
  - :mod:`.store`   — :class:`ContactStore` CRUD
  - :mod:`.prompt`  — :func:`format_contact_block`
                      (per-chat current-chatter renderer)
  - :mod:`.tools`   — the four LLM-callable tools
"""

from __future__ import annotations

from magi.agent.memory.contacts.models import (
    SOURCE_EVE,
    SOURCE_MANUAL,
    SOURCE_SYSTEM,
    ContactEntry,
)
from magi.agent.memory.contacts.prompt import format_contact_block
from magi.agent.memory.contacts.store import ContactStore, ContactView
from magi.agent.memory.contacts.tools import (
    AddContactTool,
    DeleteContactTool,
    SearchContactsTool,
    UpdateContactTool,
)


__all__ = [
    # sources
    "SOURCE_EVE",
    "SOURCE_MANUAL",
    "SOURCE_SYSTEM",
    # data
    "ContactEntry",
    "ContactStore",
    "ContactView",
    # formatter
    "format_contact_block",
    # tools
    "AddContactTool",
    "UpdateContactTool",
    "DeleteContactTool",
    "SearchContactsTool",
]