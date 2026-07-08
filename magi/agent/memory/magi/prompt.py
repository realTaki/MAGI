"""System-prompt block formatter for MAGI's mid-term memory.

Renders the MAGI's important + ongoing memory as a
Markdown block to be appended to the LLM system
prompt — same role as
:func:`magi.agent.tools.skill_loader.format_skills_block`,
but for long-term facts rather than SKILL.md metadata.

The block is empty when the MAGI has no memory (fresh
deploy). The agent loop short-circuits and uses the
soul prompt verbatim, saving a few hundred tokens per
turn.

Cap: we cap the *rendered* block at ~4 KB so a chatty
operator who asked the EVE to "remember everything"
doesn't blow the context window. The store-level
``list_for_owner(limit=50)`` already limits the row
count; this is a second gate on total bytes.

Person records do NOT render here — those live in
:mod:`magi.agent.memory.contacts` and get rendered
by a separate, per-chat formatter (the contacts of
the current chatter are included in the prompt; other
contacts are tool-loaded on demand).
"""

from __future__ import annotations

import logging
from typing import Iterable

from magi.agent.memory.magi.models import (
    KIND_IMPORTANT,
    KIND_ONGOING,
    MemoryEntry,
)
from magi.agent.memory.magi.store import MemoryView


logger = logging.getLogger("magi.agent.memory.magi.prompt")

# Soft cap on the rendered block. ~4 KB is roughly 1k
# tokens — fits the LLM's working memory comfortably
# without crowding the per-turn input.
_MAX_RENDER_BYTES = 4 * 1024


def _row_to_bullet(row: MemoryView) -> str:
    """One bullet per row.

    Sub-bullets per kind so the LLM can scan:
    "I have these policies to respect, these in-flight
    things to track." The header line is added once per
    kind by the caller; this just formats the body.
    """
    if row.kind == KIND_IMPORTANT:
        prefix = f"**{row.subject}**"
    elif row.kind == KIND_ONGOING:
        prefix = f"**{row.subject}** (in flight)"
    else:
        # Unknown kind — shouldn't happen, but be
        # defensive: still render the row.
        prefix = f"**{row.subject}** [{row.kind}]"
    if row.body and row.body != row.subject:
        return f"- {prefix} — {row.body}"
    return f"- {prefix}"


def format_memory_block(rows: Iterable[MemoryView]) -> str:
    """Render a Markdown block of MAGI's mid-term memory.

    Sections, in fixed order:

      1. **Important** — long-arc facts.
      2. **Ongoing** — work in flight.

    Returns "" when there are no rows so the agent
    loop can skip the block entirely.
    """
    rows = list(rows)
    if not rows:
        return ""

    by_kind: dict[str, list[MemoryView]] = {
        KIND_IMPORTANT: [],
        KIND_ONGOING: [],
    }
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)

    lines: list[str] = ["", "## Long-term memory (MAGI)", ""]
    lines.append(
        "下面是本 MAGI 节点的中期记忆 — 你最近在做的事情、"
        "被吩咐要记的 fact、重要的事件。Operator 让"
        "你「记住 X」时调 ``add_memory`` tool；"
        "完成时调 ``complete_memory``。更新 / 删除 "
        "对应 ``update_memory`` / ``delete_memory``。"
    )
    lines.append("")

    for kind, header in [
        (KIND_IMPORTANT, "重要的事"),
        (KIND_ONGOING, "正在进行"),
    ]:
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"### {header}")
        lines.append("")
        for row in items:
            lines.append(_row_to_bullet(row))
        lines.append("")

    rendered = "\n".join(lines).rstrip() + "\n"
    if len(rendered.encode("utf-8")) > _MAX_RENDER_BYTES:
        # Truncate at the byte cap so a runaway
        # "remember everything" instruction doesn't
        # blow the context window. Drop a one-liner
        # so the LLM knows the truncation happened.
        truncated = rendered.encode("utf-8")[:_MAX_RENDER_BYTES]
        # Avoid breaking a multi-byte char at the cut.
        truncated = truncated.decode("utf-8", errors="ignore")
        rendered = truncated + "\n\n…(memory block truncated; use the memory tools to load specific rows)\n"
        logger.warning(
            "memory block exceeded %d bytes; truncated",
            _MAX_RENDER_BYTES,
        )
    return rendered


__all__ = ["format_memory_block"]