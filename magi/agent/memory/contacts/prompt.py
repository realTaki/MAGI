"""Per-chat contact prompt formatter.

The system-prompt block for contacts is **per-chat**,
not a flat list — we render only the contact record
for the person the MAGI is currently talking to.

Why per-chat (not "all contacts"):

  - The WebUI admin is one person, the TG user is
    another. Each chat has exactly one chatter.
  - Rendering all contacts would scale badly as the
    directory grows; the per-chat version is a
    single SELECT by ``person_id`` and a small
    constant-size render.
  - Other contacts (people the chatter is NOT) are
    loaded on demand via the LLM's
    ``search_contacts`` tool — keeps the prompt
    lean and predictable.

The format is the same Markdown bullet style as
:func:`magi.agent.memory.magi.prompt.format_memory_block`
so the LLM sees a single coherent "memory" surface
in its system prompt.
"""

from __future__ import annotations

import logging
from typing import Optional

from magi.agent.memory.contacts.store import ContactView, NoteView
from magi.agent.prompts import load_contact_block_template


logger = logging.getLogger("magi.agent.memory.contacts.prompt")

# Soft cap on the rendered block. Per-chat (one
# contact) — usually well under 1 KB.
_MAX_RENDER_BYTES = 2 * 1024


def format_contact_block(
    contact: Optional[ContactView],
    *,
    display_name: Optional[str] = None,
    notes: Optional[list[NoteView]] = None,
) -> str:
    """Render a Markdown block for the current chatter.

    ``notes`` are individual ``contact_notes`` rows
    (newest first).  If not provided, falls back to
    ``contact.notes`` (legacy text column).
    """
    if contact is None:
        return ""

    lines: list[str] = ["", *load_contact_block_template().splitlines(), ""]
    header_label = (
        display_name or contact.display_name
        or contact.name or f"contact #{contact.id}"
    )
    header = f"**{header_label}**"
    if contact.role:
        lines.append(f"- {header} — role: {contact.role}")
    else:
        lines.append(f"- {header}")

    if notes:
        for n in notes:
            lines.append(f"  - {n.note}")
    elif contact.notes:
        lines.append(f"  - {contact.notes}")

    lines.append("")

    rendered = "\n".join(lines).rstrip() + "\n"
    if len(rendered.encode("utf-8")) > _MAX_RENDER_BYTES:
        truncated = rendered.encode("utf-8")[:_MAX_RENDER_BYTES]
        truncated = truncated.decode("utf-8", errors="ignore")
        rendered = truncated + "\n\n…(contact block truncated)\n"
        logger.warning(
            "contact block exceeded %d bytes; truncated",
            _MAX_RENDER_BYTES,
        )
    return rendered


__all__ = ["format_contact_block"]