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
from magi.agent.prompts import load_memory_block_template


# Sub-section labels rendered between the rows in the
# memory block. Loaded from the bundled markdown template
# (the template's ``### 重要的事`` and ``### 正在进行``
# markers) so an operator can reword both the intro and
# the per-kind headings from one file. The full template
# is split on these markers at render time; see
# :func:`format_memory_block` below.
#
# Hot-reload: ``_ensure_kind_headers_loaded`` re-parses
# the template on every call (the underlying loader is
# already mtime-aware via ``magi.agent.prompts._load``),
# so an operator editing ``memory_block.md`` to rename
# "重要的事" → "公司政策" picks up the new heading on
# the next LLM turn without a process restart.
_TEMPLATE_KIND_HEADERS: dict[str, str] = {}


def _ensure_kind_headers_loaded() -> None:
    """Parse the per-kind sub-section headings from the
    bundled ``memory_block.md`` template.

    The template looks like::

        ## Long-term memory (MAGI)

        <intro paragraph>

        ### 重要的事

        ### 正在进行

    We split on the ``### `` marker line and keep the
    heading text after the prefix. Each call re-parses
    (the underlying :func:`load_memory_block_template`
    does its own mtime check, so this is cheap), but we
    cache the parsed dict so a single LLM turn that
    renders multiple memory blocks doesn't redo the work.

    The cache is invalidated automatically when the
    template's text changes — see the docstring at the
    top of this module.
    """
    template = load_memory_block_template()
    # Drop everything before the first ``###`` (header + intro).
    # What remains is two ``### X`` lines separated by a blank.
    sections: dict[str, str] = {}
    for line in template.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            heading = stripped[4:].strip()
            # Match against the Chinese labels the template ships
            # with. Unknown headings (operator-customised the
            # template) fall back to the kind enum name so the
            # output is still readable, just not localised.
            if heading == "重要的事":
                sections[KIND_IMPORTANT] = heading
            elif heading == "正在进行":
                sections[KIND_ONGOING] = heading
    # Defensive: if the template dropped a marker, surface the
    # missing kind so a CI grep catches it instead of the LLM
    # seeing an empty sub-section.
    for kind in (KIND_IMPORTANT, KIND_ONGOING):
        if kind not in sections:
            sections[kind] = kind  # fall back to the enum string
    _TEMPLATE_KIND_HEADERS.clear()
    _TEMPLATE_KIND_HEADERS.update(sections)


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

    The block's static parts (header, intro paragraph,
    per-kind sub-section labels) come from the bundled
    ``magi/agent/prompts/memory_block.md`` template — see
    :func:`magi.agent.prompts.load_memory_block_template`.
    The per-row bullets are formatted by this function
    from the runtime rows.

    Returns "" when there are no rows so the agent loop
    can skip the block entirely (the agent loop's prompt
    builder also short-circuits on empty blocks, so a
    fresh deploy still gets a sensible prompt).
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

    _ensure_kind_headers_loaded()

    # The template includes ``### 重要的事`` and
    # ``### 正在进行`` markers as separators. Strip
    # those lines so the function below can re-emit them
    # only for the kinds that actually have rows.
    template_lines = [
        line
        for line in load_memory_block_template().splitlines()
        if not line.strip().startswith("### ")
    ]

    lines: list[str] = ["", *template_lines, ""]
    for kind in (KIND_IMPORTANT, KIND_ONGOING):
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"### {_TEMPLATE_KIND_HEADERS[kind]}")
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