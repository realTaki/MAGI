"""System-prompt block formatter for primary memory.

Renders the assigned employee's primary-scope memory
as a Markdown block to be appended to the LLM system
prompt — same role as
:func:`magi.agent.tools.skill_loader.format_skills_block`,
but for long-term facts rather than SKILL.md metadata.

The block is empty when the owner has no primary
memory (fresh deploy). The agent loop short-circuits
and uses the soul prompt verbatim, saving a few
hundred tokens per turn.

Cap: we cap the *rendered* block at ~4 KB so a chatty
operator who asked the EVE to "remember everything"
doesn't blow the context window. The store-level
``list_for_owner(limit=50)`` already limits the row
count; this is a second gate on total bytes.
"""

from __future__ import annotations

import logging
from typing import Iterable

from magi.agent.memory.models import (
    KIND_IMPORTANT,
    KIND_ONGOING,
    KIND_PERSON,
    SCOPE_PRIMARY,
    MemoryEntry,
)
from magi.agent.memory.store import MemoryView


logger = logging.getLogger("magi.agent.memory.prompt")

# Soft cap on the rendered block. ~4 KB is roughly 1k
# tokens — fits the LLM's working memory comfortably
# without crowding the per-turn input.
_MAX_RENDER_BYTES = 4 * 1024


def _row_to_bullet(row: MemoryView) -> str:
    """One bullet per row.

    Group sub-bullets per kind so the LLM can scan:
    "I have these policies to respect, these in-flight
    things to track, and these people to recognise."
    The header line is added once per kind by the
    caller; this just formats the body.
    """
    if row.kind == KIND_IMPORTANT:
        prefix = f"**{row.subject}**"
    elif row.kind == KIND_ONGOING:
        prefix = f"**{row.subject}** (in flight)"
    elif row.kind == KIND_PERSON:
        prefix = f"**{row.subject}**"
    else:
        # Unknown kind — shouldn't happen, but be
        # defensive: still render the row.
        prefix = f"**{row.subject}** [{row.kind}]"
    if row.body and row.body != row.subject:
        return f"- {prefix} — {row.body}"
    return f"- {prefix}"


def format_memory_block(rows: Iterable[MemoryView]) -> str:
    """Render a Markdown block of primary memory.

    Sections, in fixed order:

      1. **Important** — long-arc facts.
      2. **Ongoing** — work in flight.
      3. **People** — directory entries (so the LLM
         recognises names when the operator mentions
         them).

    Returns "" when there are no rows so the agent
    loop can skip the block entirely.
    """
    rows = list(rows)
    if not rows:
        return ""

    by_kind: dict[str, list[MemoryView]] = {
        KIND_IMPORTANT: [],
        KIND_ONGOING: [],
        KIND_PERSON: [],
    }
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)

    lines: list[str] = ["", "## Long-term memory", ""]
    lines.append(
        "以下是本 MAGI 节点被 `assigned` 的员工长期记录的事实。"
        "重要的事、正在进行的事、以及你认识的同事。引用时直接"
        "用这里的措辞 — 这是 operator 维护的「真值」。"
        "需要新增 / 更新 / 完成 / 删除记忆时，使用相应的 memory "
        "tool（add_memory / update_memory / complete_memory / "
        "delete_memory）。"
    )
    lines.append("")

    for kind, header in [
        (KIND_IMPORTANT, "重要的事"),
        (KIND_ONGOING, "正在进行"),
        (KIND_PERSON, "认识的人"),
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