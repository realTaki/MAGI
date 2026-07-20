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

from magi.agent.memory.contacts.store import ContactView
from magi.agent.prompts import load_contact_block_template


logger = logging.getLogger("magi.agent.memory.contacts.prompt")

# Soft cap on the rendered block. Per-chat (one
# contact) — usually well under 1 KB.
_MAX_RENDER_BYTES = 2 * 1024


def format_contact_block(
    contact: Optional[ContactView],
    *,
    display_name: Optional[str] = None,
) -> str:
    """Render a Markdown block for the current chatter.

    Returns "" when the MAGI has no contact record
    for this person. The agent loop short-circuits
    and the system prompt stays lean.

    The block is intentionally tiny (one contact)
    so the cap is rarely hit; it exists only as a
    safety net for a misbehaving tool that wrote a
    100 KB ``notes`` blob.

    ``display_name`` overrides the header's literal
    ``{person_id}`` rendering. The LLM should see the
    chatter's real name (or display_name) in the
    prompt, not the database row's integer FK —
    passing the raw id would force the model to look
    up the name via a tool call on every turn. The
    caller (the agent loop) is responsible for
    resolving the name from the Employee table; the
    formatter stays free of ORM coupling so the
    ``ContactView`` dataclass + prompt formatter
    remain testable without a database.
    """
    if contact is None:
        return ""

    lines: list[str] = ["", *load_contact_block_template().splitlines(), ""]
    # ``display_name ?? person_id`` — the caller passes
    # the resolved Employee display name when they have
    # it; fall back to the raw int FK only if not.
    header_label = display_name or str(contact.person_id)
    header = f"**{header_label}**"
    if contact.role:
        lines.append(f"- {header} — role: {contact.role}")
    else:
        lines.append(f"- {header}")
    if contact.notes:
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