"""System prompt assembly (D.4 / D.16 prompt-building).

Extracted from :mod:`magi.agent.loop` for the same reason
as :mod:`magi.agent.token_usage` and
:mod:`magi.agent.compaction`: the agent loop module is the
hot path of every chat turn, and prompt assembly
dominates the file otherwise (a 165-line function that
the loop only calls once per turn — but isn't readable
in isolation because the loop's own 400+ lines share the
file).

Two surfaces pinned:

  - :func:`read_soul` — loads ``SOUL.md`` from the
    workspace, falling back to the bundled fallback
    persona when the file is missing or empty. Used by
    both the agent loop AND
    :mod:`magi.channels.webui.api.soul` (so this module
    is the single point of contact for "what does SOUL.md
    actually mean on disk").
  - :func:`build_system_prompt` — assembles the full
    four-block prompt (SOUL + memory + contact + skills)
    in the fixed order the agent loop uses. Stateless
    from the caller's POV: takes the inputs (``state_dir``
    / ``employee_id`` / ``chat_id`` / ``soul``), returns
    a single string. The memory and contact lookups are
    done here so the LLM-facing prompt is built in one
    place; the agent loop only sees the finished string.

The ``chat_id -> Employee.telegram_id -> Employee.id ->
ContactEntry`` resolution lives here too (not in the
agent loop) so the prompt builder is self-contained.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("magi.agent.system_prompt")

# Filename expected inside the workspace root. Kept as a
# module constant so a deployer renaming the file can
# override it in one place.
SOUL_FILENAME = "SOUL.md"


def read_soul(state_dir: str) -> str:
    """Load the persona text from the workspace's ``SOUL.md``.

    Path resolution goes through :func:`magi.agent.workspace.workspace_root`
    so a deployer that sets ``MAGI_WORKSPACE_DIR`` (state lives
    outside the workspace tree) still gets the right file.

    This is a **read** function — it does not bootstrap or write
    to disk. :func:`magi.agent.workspace.bootstrap_workspace`
    runs once at boot from ``magi.node`` and is responsible
    for keeping ``SOUL.md`` in place. If the file is still
    missing (e.g. operator wiped the workspace mid-run, or the
    bundled prompt is absent from the install), we fall back to
    the bundled ``prompts/fallback_persona.md`` rather than
    write anything — the agent loop should never silently
    mutate on-disk state.
    """
    from magi.agent.prompts import load_fallback_persona
    from magi.agent.workspace import workspace_root

    soul_path = workspace_root(state_dir) / SOUL_FILENAME
    try:
        text = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return load_fallback_persona()
    text = text.strip()
    return text or load_fallback_persona()


def build_system_prompt(
    state_dir: str,
    *,
    employee_id: int,
    chat_id: str,
    soul: str,
) -> str:
    """Assemble the full system prompt for one LLM turn.

    Four blocks, concatenated in this fixed order:

      1. **SOUL** — the persona file (workspace-global).
      2. **Long-term memory** — :func:`format_memory_block`
         renders the calling admin's ``important`` +
         ``ongoing in-flight`` rows. ``completed`` ongoing
         rows are filtered out (per the store's
         ``include_completed=False`` default) so the prompt
         reflects the LLM's working set, not the audit
         trail.
      3. **Current chatter** — :func:`format_contact_block`
         renders the single contact record for the person
         the MAGI is currently talking to. Per-chat
         (not "all contacts") so the prompt stays
         constant-size regardless of directory size;
         other contacts are loaded on demand via the
         LLM's ``search_contacts`` tool.

         The lookup is keyed by ``chat_id`` (the
         per-channel delivery address — the TG chat id
         on the TG channel, ``""`` on WebUI). The
         contact row's ``person_id`` FK points at the
         Employee row, so the block carries the
         display name + role snapshot, NOT a raw
         integer id.
      4. **Available skills** — :func:`format_skills_block`
         lists the frontmatter ``name`` + ``description``
         of every registered SKILL.md. Bodies load on
         demand via ``load_skill``.

    Each block is independently short-circuit-safe:
    empty blocks render as ``""`` so a fresh deploy
    (no memory, no contact, no skills) still produces a
    sensible prompt. The result is just the SOUL when
    nothing else is registered yet.

    Side effects: this calls ``ContactStore.find_by_person``
    (one SELECT) and ``MemoryStore.list_for_owner`` (one
    SELECT, capped at the store default of 50 rows). Both
    are cheap and bounded; no N+1 risk.
    """
    from magi.agent.memory.contacts.prompt import format_contact_block
    from magi.agent.memory.contacts.store import ContactStore
    from magi.agent.memory.magi.prompt import format_memory_block
    from magi.agent.memory.magi.store import MemoryStore

    # Lazy import: chat_id → Employee lookup. Brought in
    # here (rather than at module top) to keep the import
    # graph small for the cases where the helper is never
    # called (e.g. a future "agent loop headless mode"
    # that skips the prompt assembly).
    from magi.agent.db import Employee, open_session
    from magi.agent.tools.skill_loader import (
        format_skills_block,
        get_skill_loader,
    )

    # SOUL first — establishes the persona for the rest
    # of the system prompt.
    parts: list[str] = [soul]

    # Memory block — operator-wide facts + in-flight work.
    # ``list_for_owner`` defaults: include_completed=False,
    # limit=50. Both are deliberate (see
    # :func:`magi.agent.memory.magi.store.list_for_owner`).
    try:
        memory_rows = MemoryStore(state_dir).list_for_owner(employee_id)
        memory_block = format_memory_block(memory_rows)
    except Exception:
        # The memory block is best-effort. A transient ORM
        # error here would otherwise crash the inbound
        # path — the LLM just sees a slightly thinner
        # prompt.
        logger.exception(
            "agent: memory block load failed for employee=%s; "
            "continuing without memory block",
            employee_id,
        )
        memory_block = ""
    if memory_block:
        parts.append(memory_block)

    # Contact block — per-chat (current chatter only).
    # The lookup goes ``chat_id → Employee.telegram_id``
    # → ``Employee.id`` → ``ContactEntry(person_id)``.
    # ``chat_id`` is the per-channel delivery address (the
    # TG chat id on the TG channel, ``""`` on WebUI) — not
    # the Employee row's primary key. The contact table's
    # unique key is ``(owner_id, person_id)`` where
    # ``person_id`` is the FK to ``employees.id``, so we
    # have to translate the chat address to the Employee id
    # first.
    #
    # An empty ``chat_id`` (WebUI fallback) skips the
    # lookup — the WebUI is admin-on-his-own-machine,
    # there's no "other person" to render.
    if chat_id:
        try:
            telegram_id = int(chat_id)
            from sqlalchemy import select as _sa_select
            with open_session() as db:
                person_row = db.execute(
                    _sa_select(Employee).where(
                        Employee.telegram_id == telegram_id
                    )
                ).scalar_one_or_none()
                # Resolve the display name INSIDE the
                # session — ``display_name or name`` is
                # the standard Employee-row resolution.
                # We pass it to ``format_contact_block``
                # so the rendered header reads "**Bob
                # Chen**" instead of "**2**" (the latter
                # would force the LLM to look the person
                # up via a tool call on every turn).
                if person_row is not None:
                    display_name = (
                        person_row.display_name
                        or person_row.name
                    )
                    person_id = person_row.id
                else:
                    display_name = None
                    person_id = None
            if person_id is not None:
                contact = ContactStore(state_dir).find_by_person(
                    employee_id, person_id,
                )
            else:
                contact = None
            contact_block = format_contact_block(
                contact, display_name=display_name,
            )
        except (ValueError, Exception):
            # ``int(chat_id)`` raises ValueError for a
            # non-numeric id; broader Exception catches
            # transient ORM failures. Both are non-fatal.
            logger.exception(
                "agent: contact block load failed for "
                "employee=%s chat_id=%s; continuing without "
                "contact block",
                employee_id, chat_id,
            )
            contact_block = ""
        if contact_block:
            parts.append(contact_block)

    # Skills block — last so it caps the prompt; the LLM
    # sees persona → memory → chatter → "here's what
    # else you can read" in that order.
    skills_block = format_skills_block(get_skill_loader().list())
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
