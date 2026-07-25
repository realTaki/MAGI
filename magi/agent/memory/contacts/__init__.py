"""Contact directory — what the MAGI knows about people.

The unified ``contacts`` table (see :mod:`magi.agent.db.models_contact`)
replaces the old ``contacts``, ``contact_entries``, and
``user_im_bindings`` tables. Each ``Contact`` row carries
a ``notes`` field (LLM-managed free-form markdown) and a
``source`` field (who recorded it).

The contact directory tools (``add_contact`` /
``update_contact`` / ``delete_contact`` /
``search_contacts``) operate on ``Contact.notes`` directly.

Layout:

  - :mod:`.store`   — :class:`ContactStore` CRUD
  - :mod:`.prompt`  — :func:`format_contact_block`
                      (per-chat current-contact renderer)
  - :mod:`.tools`   — the four LLM-callable tools
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
