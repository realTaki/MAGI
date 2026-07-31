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
:func:`magi.agent.memory.self.prompt.format_memory_block`
so the LLM sees a single coherent "memory" surface
in its system prompt.

The :func:`format_daily_note_block` helper renders
today's running log (``contact_notes`` rows where
``kind='daily'``) — the short-arc stream the LLM
appends to via ``update_daily_note``. The block is
operator-toggleable via ``system.show_daily_note`` in
:mod:`magi.channels.webui.api.system_settings`.
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
# Daily-note block grows faster (each chat turn appends).
# 8 KB cap mirrors the rest of the tool-result truncation
# the system applies — the morning / night report readers
# truncate the same way before injecting into their prompts.
_DAILY_NOTE_RENDER_MAX = 8 * 1024


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


def format_daily_note_block(
    note: Optional[NoteView],
    *,
    show_prompt_rules: bool = False,
) -> str:
    """Render the running-log block for today.

    ``note`` is the ``ContactNote`` row with ``kind='daily'``
    for today (or ``None`` when no deltas yet exist). The
    block is silently empty (``""``) in the no-note case so
    the system-prompt assembly helper drops it via the
    same short-circuit the other blocks use.

    ``show_prompt_rules`` toggles whether to fold the
    ``prompts/daily_note.md`` capture rules into the block
    header. Off by default — the tool description already
    carries the core intent; the full rules add ~30 lines
    of system prompt weight per turn. The operator opts in
    via ``system.show_daily_note_prompt`` for tight loops
    where the LLM tends to forget the "no trivial facts"
    constraint.
    """
    if note is None:
        return ""

    body = note.note or ""
    # Render the body verbatim — the agent loop appends new
    # lines and the LLM sees them in the order they were
    # captured. Truncate past the cap with an ellipsis so a
    # runaway delta doesn't blow the context window.
    if len(body.encode("utf-8")) > _DAILY_NOTE_RENDER_MAX:
        body_bytes = body.encode("utf-8")[:_DAILY_NOTE_RENDER_MAX]
        body = body_bytes.decode("utf-8", errors="ignore") + "\n…(今日 daily_note 已截断)"

    lines: list[str] = [
        "",
        "## 今日 daily_note",
        "",
    ]
    if show_prompt_rules:
        from magi.agent.prompts import load_daily_note_prompt
        lines.append(load_daily_note_prompt())
        lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


__all__ = ["format_contact_block", "format_daily_note_block"]
