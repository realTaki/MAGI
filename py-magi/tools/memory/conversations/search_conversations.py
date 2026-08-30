"""``search_conversations`` tool — full-text search across the
operator's chat history, with N-turn context around each hit.

Companion to ``/api/chat/search`` (D.18): same FTS5 index,
same per-contact scope, but instead of a JSON shape the
tool returns a text block the LLM can read directly.

Use case
--------

The LLM is mid-conversation and needs to recall what was
discussed earlier — "did the user mention this preference
before?", "what was that file path I gave them yesterday?"
Searching the active tail is no good (it's truncated by
compaction); the tool searches the full message log and
returns the surrounding context so the model sees what was
actually said, not just the matching token.

Scope
-----

Same per-contact scope as the WebUI's ``/api/chat/search``:
the calling operator's ``Contact.id`` (resolved by the
agent loop from the ``magi_session`` cookie on every call).
The SQL filter scopes by ``chat_conversations.contact_id``;
channel and per-channel delivery address are not part of the search predicate.

Output format
-------------

One text block per hit, capped at 20 hits per call. Each
block:

  [hit N] conversation=<id>, title="...", ts=<ISO>
    --- context (N turns before + N turns after) ---
    [user @ ts] ...
    [assistant @ ts] ...
    [assistant @ ts] <mark>matched phrase</mark> ...
    [user @ ts] ...

Where ``<mark>`` comes straight from the FTS5 ``snippet()``
output (the search backend already wraps the match in
literal ``<mark>...</mark>`` tags).

If the hit lands on an **archived** row (rolled out by
auto-compaction), the context slice falls back to the
active tail and we annotate the hit with ``(archived)``
plus the snippet — we don't have a clean way to find
"neighbouring archived messages", and a compressed
conversation by definition lost its turn-by-turn context. The
LLM gets a clear hint instead of misleading neighbours.

Output cap: the same 8 KB ceiling the other tools use —
a runaway context_n on a huge conversation can't blow up
the next LLM call.

Bus plumbing
------------

All business logic lives on the bus; this tool is
just the LLM-facing text formatter:

- :meth:`MessageBook.search` — the FTS5 query (contact_id-scoped).
- :meth:`ConversationBook.get_for_owner` — single cross-contact
  safety gate (returns ``None`` for conversations that don't
  belong to the caller).
- :meth:`MessageBook.resolve_hit` — closes the gap between
  the FTS row and the rendered context slice (fetches the
  conversation header, fetches the active+archived messages,
  classifies archived vs active, slices ±N around the hit).

Future ``/api/chat/search`` (frontend → HTTP API) will hit
the same three Book methods and return JSON instead of
text — no duplication of validation, scoping, or context
slicing.
"""

from __future__ import annotations

from typing import Any

from old_bus.firmwares.books.local.conversationBook import (
    SearchHit,
    SearchUnavailable,
)
from tools.base import Tool, ToolResult

_MAX_HITS = 20
_DEFAULT_CONTEXT_N = 5
_MAX_CONTEXT_N = 20
_MAX_OUTPUT_BYTES = 8 * 1024


class SearchConversationsTool(Tool):
    """Search the operator's chat history; return hits with
    surrounding context."""

    name = "search_conversations"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The agent worker resolves the operator's role from the
    # Contact row and filters the tool menu so non-eligible
    # callers never see these tools in the LLM's menu.
    # ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Search the operator's past conversations with EVA "
        "for messages containing a query string. Returns each "
        "match with up to ``context_n`` preceding + following "
        "messages so the model sees what was actually said "
        "around the hit (not just the matching token). Use "
        "when the user references something discussed earlier "
        '("remember when we…", "what was that…"), or '
        "when you need context that has scrolled out of the "
        "current conversation's tail. Scope: the calling operator's "
        "own history; other operators' conversations are not "
        "reachable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": (
                    "Search query. Whitespace-separated tokens "
                    "are matched as substrings; CJK runs of 3+ "
                    "characters work (the FTS5 index uses "
                    'trigram tokenisation). Operators ``"``, '
                    "``*``, ``AND``/``OR``/``NOT`` etc. are "
                    "escaped by the backend — you don't need "
                    "to sanitise the input yourself."
                ),
            },
            "context_n": {
                "type": "integer",
                "description": (
                    "How many surrounding messages to include "
                    "before + after each hit. Defaults to 5. "
                    "Max 20. Set 0 to return only the matching "
                    "snippet without neighbours."
                ),
                "minimum": 0,
                "maximum": _MAX_CONTEXT_N,
            },
            "limit": {
                "type": "integer",
                "description": ("Max number of hits to return. Defaults to 10; capped at 20."),
                "minimum": 1,
                "maximum": _MAX_HITS,
            },
        },
        "required": ["q"],
    }

    @Tool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        q = kwargs.get("q")
        if not isinstance(q, str) or not q.strip():
            return ToolResult(
                content=("search_conversations: ``q`` is required and must be a non-empty string"),
                is_error=True,
            )

        context_n = kwargs.get("context_n", _DEFAULT_CONTEXT_N)
        if not isinstance(context_n, int):
            return ToolResult(
                content=(
                    f"search_conversations: ``context_n`` must be an "
                    f"integer, got {type(context_n).__name__}"
                ),
                is_error=True,
            )
        context_n = max(0, min(context_n, _MAX_CONTEXT_N))

        limit = kwargs.get("limit", 10)
        if not isinstance(limit, int):
            return ToolResult(
                content=(
                    f"search_conversations: ``limit`` must be an integer, got {type(limit).__name__}"
                ),
                is_error=True,
            )
        limit = max(1, min(limit, _MAX_HITS))

        # Scope: the calling operator's contact_id. Cross-
        # platform: every conversation row whose ``contact_id``
        # matches — webui conversations AND any TG / future
        # IM conversations handled by that operator all
        # match. Channel and per-channel delivery address
        # are not part of the search predicate.
        contact_id = int(kwargs.get("contact_id") or 0)

        try:
            hits, total = self.bus.messages_book.search(
                contact_id=contact_id,
                q=q,
                limit=limit,
            )
        except SearchUnavailable as e:
            return ToolResult(content=f"search_conversations: {e}", is_error=True)
        except Exception as e:
            # FTS5 syntax error post-sanitisation shouldn't
            # happen, but defend with a clear message
            # instead of a 500.
            return ToolResult(
                content=f"search_conversations: query rejected by FTS5: {e}",
                is_error=True,
            )

        if not hits:
            return ToolResult(content=(f"search_conversations: no matches for q={q!r} (total={total})"))

        # Format each hit with its surrounding context.
        # Cap the running output at ``_MAX_OUTPUT_BYTES`` so
        # a runaway context_n + limit can't blow up the next
        # LLM call. Once we hit the cap, drop remaining
        # hits and append a clear summary line.
        blocks: list[str] = []
        bytes_used = 0
        # Sentinel: ``None`` = "nothing was truncated". The
        # previous initialiser used ``len(hits)``, which made
        # every successful search report "N additional hits
        # omitted" even when nothing was truncated — because
        # ``if truncated_at:`` is truthy whenever ``len(hits) >
        # 0``. The footer only makes sense once truncation has
        # actually fired.
        truncated_at: int | None = None
        for _i, hit in enumerate(hits, start=1):
            block = _format_hit_block(
                hit,
                context_n,
                self.bus,
                contact_id,
            )
            block_bytes = len(block.encode("utf-8"))
            if bytes_used + block_bytes > _MAX_OUTPUT_BYTES:
                # ``i`` is the 1-indexed position of the hit we
                # *would have* rendered next; ``len(blocks)`` is
                # the count we actually rendered. Everything
                # from ``len(blocks)+1`` onwards is omitted.
                truncated_at = len(hits) - len(blocks)
                break
            blocks.append(block)
            bytes_used += block_bytes

        header = (
            f"search_conversations: q={q!r}, {total} match(es) "
            f"scoped to contact_id={contact_id}; "
            f"returning {len(blocks)} of {len(hits)} hit(s) "
            f"with ±{context_n} message context each\n"
        )
        body = "\n\n".join(blocks)
        footer = ""
        if truncated_at:
            footer = (
                f"\n\n…({truncated_at} additional hit(s) "
                f"omitted — output cap {_MAX_OUTPUT_BYTES // 1024} KB reached)"
            )

        return ToolResult(content=header + body + footer)


def _format_hit_block(hit: SearchHit, context_n: int, bus, contact_id: int) -> str:
    """Build the text block for one FTS5 hit: header +
    surrounding context.

    Pure formatting — all cross-contact validation, conversation
    lookup, message fetch, active/archive classification,
    and context slicing live in
    :meth:`MessageBook.resolve_hit`. This function is just
    the LLM-facing text renderer; the future
    ``/api/chat/search`` HTTP endpoint will write a JSON
    formatter over the same ``ResolvedHit`` envelope.
    """
    resolved = bus.messages_book.resolve_hit(
        contact_id=contact_id,
        hit=hit,
        context_n=context_n,
        conversations_book=bus.conversations_book,
    )
    if resolved is None:
        # Hit's conversation doesn't belong to the caller (or
        # the row was deleted between FTS and read). Emit
        # a generic placeholder rather than leak metadata.
        return (
            f"[hit] conversation={hit.conversation_id}, ts={hit.ts}, "
            f"role={hit.role}, channel={hit.channel}, "
            f"delivery_address={hit.delivery_address} — "
            f"conversation no longer accessible to caller"
        )

    header = (
        f"[hit] conversation={resolved.conversation.conversation_id}, "
        f"title={resolved.conversation.title!r}, ts={hit.ts}, "
        f"role={hit.role}, channel={hit.channel}, "
        f"delivery_address={hit.delivery_address}" + (" (archived)" if resolved.is_archived else "")
    )

    if not resolved.messages_with_hit:
        # Either archived (no clean neighbour) or the caller
        # asked for snippet-only (``context_n == 0``).
        return f"{header}\nsnippet: {hit.snippet}"

    context_lines: list[str] = []
    for j, m in enumerate(resolved.messages_with_hit):
        marker = "  >>" if j == resolved.hit_position else "    "
        text = m.text if j != resolved.hit_position else hit.snippet
        context_lines.append(f"{marker} [{m.role} @ {m.ts}] {text}")
    context = "\n".join(context_lines)
    return f"{header}\n--- context (idx {resolved.hit_position}) ---\n{context}"
