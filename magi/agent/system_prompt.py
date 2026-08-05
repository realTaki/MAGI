"""System prompt assembly (D.4 / D.16 prompt-building).

Kept separate from :mod:`magi.agent.token_usage` and
:mod:`magi.agent.compaction`: prompt assembly is reusable and does
not belong in the provider-step implementation.

Two surfaces pinned:

  - :func:`read_soul` — loads ``SOUL.md`` from the
    workspace, falling back to the bundled fallback
    persona when the file is missing or empty. Used by
    both the agent runtime AND
    :mod:`magi.channels.api.soul` (so this module
    is the single point of contact for "what does SOUL.md
    actually mean on disk").
  - :func:`build_system_prompt` — assembles the full prompt
    in the fixed order the agent loop uses. Stateless
    from the caller's POV: takes ``uid`` / ``soul``,
    returns a single string. The
    memory and contact lookups are done here so the
    LLM-facing prompt is built in one place; the agent
    loop only sees the finished string.

The ``uid -> Contact row -> ContactEntry`` resolution
lives here too (not in the agent loop) so the prompt
builder is self-contained.
"""

from __future__ import annotations

import logging
from pathlib import Path

from magi.launcher.paths import workspace_dir

logger = logging.getLogger("magi.agent.system_prompt")

# Filename expected inside the workspace root. Kept as a
# module constant so a deployer renaming the file can
# override it in one place.
SOUL_FILENAME = "SOUL.md"


def _format_memory_block(rows) -> str:
    return "" if not rows else "## Long-term memory\n" + "\n".join(f"- [{r.kind}] {r.subject}: {r.body}" for r in rows)


def _format_contact_block(contact, notes) -> str:
    if contact is None:
        return ""
    name = contact.display_name or contact.name
    lines = [f"## Current chatter\nName: {name}"]
    if contact.notes:
        lines.append(contact.notes)
    lines.extend(f"- {note.note}" for note in notes or [])
    return "\n".join(lines)


def _format_daily_note_block(note) -> str:
    return f"## Daily note\n{note.note}" if note is not None and note.note else ""


def read_soul() -> str:
    """Load the persona text from the workspace's ``SOUL.md``.

    The workspace path is resolved from ``MAGI_WORKSPACE_DIR``
    (K8s Pod) or ``HOST_WORKSPACE_DIR`` (Local Profile) — never a
    hardcoded path.

    This is a **read** function — it does not bootstrap or write
    to disk. The workspace bootstrap runs once at boot from
    ``magi.__main__`` and is responsible for keeping ``SOUL.md``
    in place. If the file is still missing (e.g. operator wiped
    the workspace mid-run, or the bundled prompt is absent from
    the install), we fall back to the bundled
    ``prompts/fallback_persona.md`` rather than write anything —
    the agent loop should never silently mutate on-disk state.
    """
    from magi.prompts import load_fallback_persona

    soul_path = workspace_dir() / SOUL_FILENAME
    try:
        text = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return load_fallback_persona()
    text = text.strip()
    return text or load_fallback_persona()


def build_system_prompt(
    *,
    uid: int,
    soul: str,
) -> str:
    """Assemble the full system prompt for one LLM turn.

    Six blocks, concatenated in this fixed order:

      1. **SOUL** — the persona file (workspace-global).
      2. **Instructions** — the MAGI's personal instruction plus every
         MAGIS and role instruction from its memberships.
      3. **Long-term memory** — :func:`format_memory_block`
         renders the calling User's ``important`` +
         ``ongoing in-flight`` rows. ``completed`` ongoing
         rows are filtered out (per the store's
         ``include_completed=False`` default) so the prompt
         reflects the LLM's working set, not the audit
         trail.
      4. **Current chatter** — :func:`format_contact_block`
         renders the :class:`ContactEntry` row scoped to
         ``(uid, uid)``: the User's own self-record, the
         same lookup the user's ``add_contact_note`` /
         ``search_contacts`` tools maintain.
      5. **Daily note** — :func:`format_daily_note_block`
         renders today's running log (``contact_notes``
         where ``kind='daily'``). The LLM appends to it via
         the ``update_daily_note`` tool. Operator-toggleable
         via ``system.show_daily_note`` (default ON); the
         capture-rules prompt fold-in is gated separately by
         ``system.show_daily_note_prompt`` for noisy loops.
      6. **Available skills** — :func:`format_skills_block`
         lists the frontmatter ``name`` + ``description``
         of every registered SKILL.md. Bodies load on
         demand via ``load_skill``.

    Each block is independently short-circuit-safe:
    empty blocks render as ``""`` so a fresh deploy
    (no memory, no contacts, no skills) still produces
    a sensible prompt. The result is just the SOUL when
    nothing else is registered yet.

    Side effects: this calls ``bus.memory.list_for_owner``
    (one SELECT, capped at 50 rows), ``ContactStore.find_by_person``
    (single primary-key lookup), a one-row ``Contact``
    read for the chatter's display_name, and
    ``get_skill_loader`` (filesystem scan). Each is
    bounded; no N+1 risk.
    """
    from magi.skills import format_skills_block, get_skill_metas
    from magi.bus import get_bus

    # SOUL first — establishes the persona for the rest
    # of the system prompt.
    parts: list[str] = [soul]

    from magi.agent.instructions import runtime_instruction_block
    instruction_block = runtime_instruction_block()
    if instruction_block:
        parts.append(instruction_block)

    # Memory block — User-wide facts + in-flight work.
    try:
        memory_rows = get_bus().memory.list_for_owner(uid)
        memory_block = _format_memory_block(memory_rows)
    except Exception:
        logger.exception(
            "agent: memory block load failed for uid=%s; "
            "continuing without memory block",
            uid,
        )
        memory_block = ""
    if memory_block:
        parts.append(memory_block)

    # Current-chatter block — the User's self-contact
    # entry (the directory the LLM writes to via
    # ``add_contact_note`` / ``update_contact_note``). When no
    # record exists yet, the block is silently dropped
    # so a fresh deploy doesn't carry an empty
    # "Current chatter" header.
    contact_block = ""
    try:
        contacts = get_bus().contacts
        contact = contacts.get(uid)
        notes = contacts.list_notes(uid) if contact else None
        contact_block = _format_contact_block(contact, notes)
    except Exception:
        logger.exception(
            "agent: contact block load failed for uid=%s; "
            "continuing without contact block",
            uid,
        )
    if contact_block:
        parts.append(contact_block)

    # Daily-note block — today's running log. Gated by
    # ``system.show_daily_note`` (default ON); the capture
    # rules only fold in when the operator explicitly opts
    # in via ``system.show_daily_note_prompt`` (default
    # OFF — the tool description already restates the
    # core intent).
    show_daily_note, show_daily_note_prompt = get_bus().settings.show_daily_note()
    if show_daily_note:
        daily_block = ""
        try:
            note = get_bus().contacts.read_daily_note(uid)
            daily_block = _format_daily_note_block(note)
        except Exception:
            logger.exception(
                "agent: daily note block load failed for uid=%s; "
                "continuing without daily note block",
                uid,
            )
        if daily_block:
            parts.append(daily_block)

    # Skills block — last so it caps the prompt.
    skills_block = format_skills_block(get_skill_metas())
    if skills_block:
        parts.append(skills_block)

    rendered = "\n\n".join(parts).strip()
    # If every block was empty (highly unlikely — at
    # minimum the bundled persona returns a non-empty
    # fallback), fall back to the soul alone rather
    # than the empty string the LLM SDK would reject.
    return rendered or soul


__all__ = [
    "SOUL_FILENAME",
    "read_soul",
    "build_system_prompt",
]
