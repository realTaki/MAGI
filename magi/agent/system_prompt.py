"""System prompt assembly — bus only."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.old_bus import Bus

from magi.old_bus.firmwares.books.local import ActionPriority

logger = logging.getLogger("magi.agent.system_prompt")


def _format_memory_block(rows) -> str:
    return (
        ""
        if not rows
        else "## Long-term memory\n"
        + "\n".join(
            f"- [{getattr(r, 'kind', '')}] {getattr(r, 'subject', '')}: {getattr(r, 'body', '')}"
            for r in rows
        )
    )


def _format_contact_block(contact, notes) -> str:
    if contact is None:
        return ""
    name = getattr(contact, "display_name", None) or getattr(contact, "name", "")
    lines = [f"## Current chatter\nName: {name}"]
    for note in notes or []:
        body = getattr(note, "note", "")
        if body:
            lines.append(f"- {body}")
    return "\n".join(lines)


def _format_daily_note_block(note) -> str:
    if note is None:
        return ""
    body = getattr(note, "note", None)
    return f"## Daily note\n{body}" if body else ""


def _format_collaboration_directory(bus: Bus, *, magi_id: int | None) -> str:
    """Render public peers for intentional, tool-mediated A2A collaboration."""
    if magi_id is None or bus.memberships_book is None:
        return ""
    try:
        members = bus.memberships_book.list_collaboration_directory(magi_id=magi_id)
    except Exception:
        logger.exception("collaboration directory lookup failed for magi_id=%s", magi_id)
        return ""
    if not members:
        return ""
    lines = [
        "## MAGIS collaboration directory",
        "Use message_magi only when a peer's public role and responsibility fit the work. "
        "A notify does not expect a reply; a request receives at most one reply.",
    ]
    for member in members:
        marker = " (you)" if member.magi_id == magi_id else ""
        responsibility = member.responsibility.strip() or "No public responsibility provided."
        lines.append(
            f"- MAGI #{member.magi_id} {member.magi_name}{marker} — "
            f"role: {member.role_name}; responsibility: {responsibility}"
        )
    return "\n".join(lines)


def read_soul(*, bus: Bus) -> str:
    """Read the SOUL persona from the AgentWorker-owned prompt records."""
    return (bus.prompt_book.get(key="agent/soul") or "").strip()


def build_system_prompt(
    *,
    contact_id: int,
    soul: str,
    bus: Bus,
    magi_id: int | None = None,
) -> str:
    """Assemble the full system prompt for one LLM turn.

    Eight blocks, ordered by stability (most stable first) with the most
    volatile block kept at the tail so it sits closest to the user message
    and benefits from recency bias:

        SOUL → Instructions → Directory → Skills → Memory
              → Contact → Action items → Daily note

    The first three are runtime constants; Skills is a semi-static
    reference; Memory / Contact are per-contact persistent context;
    Action items and Daily note are per-turn state, with Daily note being
    the most volatile (rewritten or appended many times a day).
    """
    parts: list[str] = [soul]

    # 2. Instructions — runtime, magi/team/role-aware operating context.
    from magi.agent.instructions import runtime_instruction_block

    instruction_block = runtime_instruction_block(bus, magi_id=magi_id)
    if instruction_block:
        parts.append(instruction_block)

    # 3. MAGIS collaboration directory — peer roster for A2A steering.
    directory_block = _format_collaboration_directory(bus, magi_id=magi_id)
    if directory_block:
        parts.append(directory_block)

    # 4. Skills — semi-static reference.  Placed here (after the runtime
    # identity blocks, before per-turn state) so it benefits from the
    # higher-attention zone near the system prompt core instead of being
    # pushed against the recency-biased tail where it would dilute the
    # modeler's view of fresh state.
    skills_book = getattr(bus, "skills_book", None)
    if skills_book is not None:
        try:
            metas = skills_book.list()
            if metas:
                skills_header = bus.prompt_book.get(key="agent/skills_block") or ""
                lines = ["", *skills_header.splitlines(), ""]
                for s in metas:
                    name = getattr(s, "name", "") or ""
                    desc = getattr(s, "description", "") or ""
                    ver = getattr(s, "version", None)
                    if ver:
                        lines.append(f"- **{name}** (v{ver}) — {desc}")
                    else:
                        lines.append(f"- **{name}** — {desc}")
                parts.append("\n".join(lines))
        except Exception:
            logger.exception("skills block load failed")

    # 5. Memory — per-contact persistent context.
    try:
        rows = bus.memory_book.list_by_owner(contact_id=contact_id)
        block = _format_memory_block(rows)
    except Exception:
        logger.exception("memory block load failed for contact_id=%s", contact_id)
        block = ""
    if block:
        parts.append(block)

    # 6. Current chatter — this contact's profile + notes.
    try:
        contact = bus.contacts_book.get(contact_id)
        notes = bus.contact_notes_book.list_for_contact(contact_id=contact_id) if contact else None
        contact_block = _format_contact_block(contact, notes)
    except Exception:
        logger.exception("contact block load failed for contact_id=%s", contact_id)
        contact_block = ""
    if contact_block:
        parts.append(contact_block)

    # 7. Open high-priority action items.  ActionItem remains local and is
    # scoped by the current Contact id, including a MAGIS admin's local
    # projection; never leak another user's reminder history into this prompt.
    try:
        action_items = [
            item
            for item in bus.action_items_book.list_actions(
                owner_contact_id=contact_id,
                include_completed=False,
            )
            if item.priority == ActionPriority.HIGH
        ]
        if action_items:
            lines = ["## Open high-priority action items"]
            for item in action_items[:10]:
                detail = f" — {item.description}" if item.description else ""
                lines.append(f"- {item.title}{detail}")
            parts.append("\n".join(lines))
    except Exception:
        logger.exception("high-priority action item block load failed")

    # 8. Daily note — the most volatile block; rewritable many times per
    # day.  Kept at the tail so it sits closest to the user message and
    # benefits from the strongest recency-bias attention.
    try:
        note = bus.contact_notes_book.read_daily_note(contact_id=contact_id)
        daily_block = _format_daily_note_block(note)
    except Exception:
        logger.exception("daily note block load failed for contact_id=%s", contact_id)
        daily_block = ""
    if daily_block:
        parts.append(daily_block)

    rendered = "\n\n".join(parts).strip()
    return rendered or soul


__all__ = ["read_soul", "build_system_prompt"]
