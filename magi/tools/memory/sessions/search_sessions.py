"""``search_sessions`` tool — full-text search across the
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
The SQL filter scopes by ``chat_sessions.uid``;
channel and per-channel delivery address are not part of the search predicate.

Output format
-------------

One text block per hit, capped at 20 hits per call. Each
block:

  [hit N] session=<id>, title="...", ts=<ISO>
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
session by definition lost its turn-by-turn context. The
LLM gets a clear hint instead of misleading neighbours.

Output cap: the same 8 KB ceiling the other tools use —
a runaway context_n on a huge session can't blow up the
next LLM call.

Bus plumbing
------------

Lives on the new_bus: the FTS5 query goes through
:class:`magi.new_bus.library.local.sessionBook.MessageBook.search`,
the surrounding-context slice goes through
:meth:`MessageBook.list_for_session` (one fetch, with
``include_archived=True`` so we can tell an archived hit
from an active one by the ``archived`` flag), and the
session header + cross-contact defence go through
:meth:`SessionBook.get_for_owner`. That single helper
returns ``None`` when the session doesn't belong to the
caller, so this tool and the future ``/api/chat/search``
HTTP endpoint share one place to enforce per-contact
scoping — the new_bus's :meth:`SessionBook.get` only
accepts ``session_id`` (the old bus's loader validated
``uid`` itself), so the check has to live somewhere.
"""

from __future__ import annotations

from typing import Any

from magi.new_bus.library.local.sessionBook import (
    SearchHit,
    SearchUnavailable,
)
from magi.tools.base import Tool, ToolContext, ToolResult

_MAX_HITS = 20
_DEFAULT_CONTEXT_N = 5
_MAX_CONTEXT_N = 20
_MAX_OUTPUT_BYTES = 8 * 1024


class SearchSessionsTool(Tool):
    """Search the operator's chat history; return hits with
    surrounding context."""

    name = "search_sessions"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Search the operator's past conversations with EVA "
        "for messages containing a query string. Returns each "
        "match with up to ``context_n`` preceding + following "
        "messages so the model sees what was actually said "
        "around the hit (not just the matching token). Use "
        "when the user references something discussed earlier "
        "(\"remember when we…\", \"what was that…\"), or "
        "when you need context that has scrolled out of the "
        "current session's tail. Scope: the calling operator's "
        "own history; other operators' sessions are not "
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
                    "trigram tokenisation). Operators ``\"``, "
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
                "description": (
                    "Max number of hits to return. Defaults to "
                    "10; capped at 20."
                ),
                "minimum": 1,
                "maximum": _MAX_HITS,
            },
        },
        "required": ["q"],
    }

    @Tool.require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        q = kwargs.get("q")
        if not isinstance(q, str) or not q.strip():
            return ToolResult(
                content=(
                    "search_sessions: ``q`` is required and must "
                    "be a non-empty string"
                ),
                is_error=True,
            )

        context_n = kwargs.get("context_n", _DEFAULT_CONTEXT_N)
        if not isinstance(context_n, int):
            return ToolResult(
                content=(
                    f"search_sessions: ``context_n`` must be an "
                    f"integer, got {type(context_n).__name__}"
                ),
                is_error=True,
            )
        context_n = max(0, min(context_n, _MAX_CONTEXT_N))

        limit = kwargs.get("limit", 10)
        if not isinstance(limit, int):
            return ToolResult(
                content=(
                    f"search_sessions: ``limit`` must be an "
                    f"integer, got {type(limit).__name__}"
                ),
                is_error=True,
            )
        limit = max(1, min(limit, _MAX_HITS))

        # Scope: the calling operator's uid. Cross-
        # platform: every session row whose ``uid``
        # matches — webui conversations AND any TG / future
        # IM conversations handled by that operator all
        # match. Channel and per-channel delivery address
        # are not part of the search predicate.
        uid = ctx.uid

        try:
            hits, total = ctx.bus.messages_book.search(
                uid=uid, q=q, limit=limit,
            )
        except SearchUnavailable as e:
            return ToolResult(content=f"search_sessions: {e}", is_error=True)
        except Exception as e:
            # FTS5 syntax error post-sanitisation shouldn't
            # happen, but defend with a clear message
            # instead of a 500.
            return ToolResult(
                content=f"search_sessions: query rejected by FTS5: {e}",
                is_error=True,
            )

        if not hits:
            return ToolResult(
                content=(
                    f"search_sessions: no matches for q={q!r} "
                    f"(total={total})"
                )
            )

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
        for i, hit in enumerate(hits, start=1):
            block = _format_hit_block(
                hit, context_n,
                ctx.bus,
                uid,
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
            f"search_sessions: q={q!r}, {total} match(es) "
            f"scoped to uid={uid}; "
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


def _format_hit_block(hit: SearchHit, context_n: int, bus, uid: int) -> str:
    """Build the text block for one FTS5 hit: header +
    surrounding context.

    The hit may land on an active or archived message. The
    new_bus keeps both in the same ``chat_messages`` table
    with an ``archived`` flag (the old bus split them into
    ``Session.messages`` / ``Session.archive`` lists — that
    distinction has been folded into the row), so one
    :meth:`MessageBook.list_for_session` call (with
    ``include_archived=True``) gives us everything we need
    to render either kind of block.

    For archived hits there's no clean "neighbour" (auto-
    compaction rolled the adjacent turns out by design), so
    we annotate ``(archived)`` and return just the snippet —
    the LLM gets a clear hint instead of misleading
    neighbours. For active hits we slice the **active
    subset** of the message list around the hit's index in
    that subset (NOT the combined list — archived rows are
    not "around" an active conversation by definition).

    Defence-in-depth: the FTS query is already scoped by
    ``s.uid = :uid``, but the session lookup goes through
    :meth:`SessionBook.get_for_owner` (the new_bus's
    :meth:`SessionBook.get` only accepts ``session_id``).
    That helper returns ``None`` when the hit's session
    doesn't belong to the caller — closing the gap so this
    tool and the future ``/api/chat/search`` HTTP endpoint
    share one place to enforce per-contact scoping.
    """
    session = bus.sessions_book.get_for_owner(
        uid=uid, session_id=hit.session_id,
    )
    if session is None:
        # Race: hit row was deleted between FTS5 hit and
        # read, or the search joined to a session that
        # somehow doesn't belong to the caller. Defend
        # rather than leak the row's metadata.
        return (
            f"[hit] session={hit.session_id}, ts={hit.ts}, "
            f"role={hit.role}, channel={hit.channel}, "
            f"delivery_address={hit.delivery_address} — "
            f"session no longer accessible to caller"
        )

    # One fetch covers both branches: active hits and
    # archived hits (the latter is detected via
    # ``m.archived == 1``). The list is sorted by row id,
    # which is monotonic per session (auto-compaction only
    # flips the flag — never reorders rows).
    messages = bus.messages_book.list_for_session(
        session_id=hit.session_id, include_archived=True,
    )

    # Find hit's combined index first, then split into
    # active vs archive branches.
    hit_idx = _index_of_message_id(messages, hit.message_id)
    if hit_idx is None:
        # Race: row was deleted between FTS5 hit and read.
        return (
            f"[hit] session={hit.session_id}, ts={hit.ts}, "
            f"role={hit.role}, channel={hit.channel}, "
            f"delivery_address={hit.delivery_address} — "
            f"hit message no longer in session"
        )
    is_archived = messages[hit_idx].archived == 1

    header = (
        f"[hit] session={session.session_id}, "
        f"title={session.title!r}, ts={hit.ts}, "
        f"role={hit.role}, channel={hit.channel}, delivery_address={hit.delivery_address}"
        + (" (archived)" if is_archived else "")
    )

    if is_archived or context_n == 0:
        # Either archived (no clean neighbour) or caller
        # asked for snippet-only.
        return f"{header}\nsnippet: {hit.snippet}"

    # Active hit: slice the active subset of the messages
    # list around the hit. Archived rows were rolled out
    # by auto-compaction and don't form a coherent
    # "around the hit" neighbourhood; we want the
    # conversation flow that the user actually saw.
    active_msgs = [m for m in messages if m.archived == 0]
    active_idx = _index_of_message_id(active_msgs, hit.message_id)
    if active_idx is None:
        # Hit row's archived flag flipped between the
        # combined read and now — race with compaction.
        return f"{header}\nsnippet: {hit.snippet}"

    lo = max(0, active_idx - context_n)
    hi = min(len(active_msgs), active_idx + context_n + 1)
    context_msgs = active_msgs[lo:hi]
    context_lines = []
    for j, m in enumerate(context_msgs):
        actual_idx = lo + j
        marker = "  >>" if actual_idx == active_idx else "    "
        text = m.text
        if actual_idx == active_idx:
            # Re-attach the snippet's <mark> highlighting
            # so the LLM sees where in the message the hit
            # landed.
            text = hit.snippet
        context_lines.append(
            f"{marker} [{m.role} @ {m.ts}] {text}"
        )
    context = "\n".join(context_lines)
    return f"{header}\n--- context (idx {active_idx}) ---\n{context}"


def _index_of_message_id(messages, message_id: str) -> int | None:
    """Find ``message_id`` in the messages list. Returns the
    index, or ``None`` if not present."""
    for i, m in enumerate(messages):
        if m.message_id == message_id:
            return i
    return None