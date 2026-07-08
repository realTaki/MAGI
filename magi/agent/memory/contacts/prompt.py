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


logger = logging.getLogger("magi.agent.memory.contacts.prompt")

# Soft cap on the rendered block. Per-chat (one
# contact) — usually well under 1 KB.
_MAX_RENDER_BYTES = 2 * 1024


def format_contact_block(contact: Optional[ContactView]) -> str:
    """Render a Markdown block for the current chatter.

    Returns "" when the MAGI has no contact record
    for this person. The agent loop short-circuits
    and the system prompt stays lean.

    The block is intentionally tiny (one contact)
    so the cap is rarely hit; it exists only as a
    safety net for a misbehaving tool that wrote a
    100 KB ``notes`` blob.
    """
    if contact is None:
        return ""

    lines: list[str] = ["", "## Current chatter", ""]
    lines.append(
        "你正在与以下人员对话。这是 MAGI 知道的关于他/她的"
        "信息（来源：contacts 表）。如果信息过时或需要补充，"
        "用 ``update_contact`` 或 ``search_contacts`` tool。"
    )
    lines.append("")
    header = f"**{contact.person_id}**"  # 实际渲染时 caller 会用真名
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