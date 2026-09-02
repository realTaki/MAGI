"""``add_contact`` — create a Contact, optionally with a first note.

Jobs:
  CreateContactJob — insert name/nickname/role.
  GetContactJob — return the created row to the LLM.
  CreateContactNoteJob — optional initial permanent note.
  GetContactNoteJob — return that note if one was written.
"""

from __future__ import annotations

import logging
from typing import Any

from bus import (
    ContactRole,
    CreateContactJob,
    CreateContactNoteJob,
    GetContactJob,
    GetContactNoteJob,
    NoteKind,
)
from tools.BaseTool import BaseTool, ToolResult

logger = logging.getLogger("tools.memory.add_contact")

_PUBLISHER = "tools"
_ROLES = {
    "assigned": ContactRole.AUTHORIZED,
    "authorized": ContactRole.AUTHORIZED,
    "guest": ContactRole.STRANGER,
    "stranger": ContactRole.STRANGER,
    "magi": ContactRole.MAGI,
    "third_party_agent": ContactRole.THIRD_PARTY_AGENT,
}


class AddContactTool(BaseTool):
    """Create a new contact."""

    name = "add_contact"
    description = (
        "Create a new contact. Name is required. nickname, role, and "
        "notes (initial permanent note) are optional. To add notes about "
        "an existing contact, use save_contact_note."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Contact name (required).",
            },
            "nickname": {
                "type": "string",
                "description": "Display name (optional).",
            },
            "role": {
                "type": "string",
                "enum": sorted(_ROLES),
                "default": "stranger",
                "description": (
                    "authorized | stranger | magi | third_party_agent. "
                    "assigned maps to authorized; guest maps to stranger."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Initial permanent note (optional).",
            },
        },
        "required": ["name"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        name = kwargs.get("name")
        if not isinstance(name, str) or not name.strip():
            return ToolResult.err("name is required (non-empty string)")
        role_str = kwargs.get("role") or "stranger"
        role = _ROLES.get(str(role_str).strip().lower())
        if role is None:
            return ToolResult.err(f"role must be one of {sorted(_ROLES)!r}, got {role_str!r}")
        nickname = kwargs.get("nickname")
        created = await self.publish(
            CreateContactJob(
                publisher=_PUBLISHER,
                name=name.strip(),
                nickname=None if nickname is None else str(nickname),
                role=role,
            )
        )
        if created is None or created.contact_id is None:
            return ToolResult.err("contact book is not available")
        fetched = await self.publish(
            GetContactJob(publisher=_PUBLISHER, contact_id=created.contact_id)
        )
        contact = None if fetched is None else fetched.contact
        payload: dict[str, Any] = {
            "created": None if contact is None else contact.to_dict(),
        }
        initial_note = kwargs.get("notes")
        if initial_note and str(initial_note).strip():
            note_created = await self.publish(
                CreateContactNoteJob(
                    publisher=_PUBLISHER,
                    contact_id=created.contact_id,
                    note=str(initial_note).strip(),
                    kind=NoteKind.PERMANENT,
                )
            )
            if note_created is None or note_created.contact_note_id is None:
                return ToolResult.err("contact note book is not available")
            note_fetched = await self.publish(
                GetContactNoteJob(
                    publisher=_PUBLISHER,
                    contact_note_id=note_created.contact_note_id,
                )
            )
            payload["initial_note"] = (
                None if note_fetched is None or note_fetched.contact_note is None
                else note_fetched.contact_note.to_dict()
            )
            logger.info(
                "add_contact: contact=%s created with initial note=%s",
                created.contact_id,
                note_created.contact_note_id,
            )
            return ToolResult.ok(payload)

        logger.info("add_contact: contact=%s created", created.contact_id)
        return ToolResult.ok(payload)
