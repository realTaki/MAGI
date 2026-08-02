"""Chat-history FTS5 search — neutral domain query.

Package-neutral home for the FTS5 query helper that the agent tool
(:mod:`magi.tools.search_sessions`) and the WebUI API both call.
The HTTP wrapper (FastAPI route, Pydantic response model,
admin-gate plumbing) stays in
:mod:`magi.channels.webui.api.chat_search`; only the data path
moves here.

Why this lives under :mod:`magi.agent.memory.session`:

  The query reads the per-contact chat history (chat_sessions /
  chat_messages / chat_messages_fts) which are owned by the
  session-domain package. The agent tool
  (``magi.tools.search_sessions``) needs to read this too and
  must not reach back into ``channels.webui.api.*`` to do so
  (design §18 forbids agent → channel implementation).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from magi.db import open_session

logger = logging.getLogger("magi.agent.memory.session.search")


class SearchHit(BaseModel):
    """One FTS5 match with surrounding session metadata."""

    session_id: str
    message_id: str
    role: str
    ts: str
    snippet: str
    title: str | None = None
    score: float
    delivery_address: str | None = None
    channel: str


class SearchUnavailable(Exception):
    """Raised when FTS5 isn't compiled into the project's SQLite.

    The HTTP route translates this to ``503 search.unavailable``;
    the agent tool returns it as a ``ToolResult(is_error=True, ...)``
    so the LLM sees the same hint.
    """


def _chat_search_available() -> bool:
    """Probe whether the FTS5 virtual table exists.

    Cheap (one ``sqlite_master`` row lookup). Runs on every search
    request; the alternative would be a module-level cache that
    could lie after a schema rebuild, so the freshness is worth
    the microsecond.
    """
    with open_session() as db:
        row = db.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='chat_messages_fts'"
            )
        ).first()
    return row is not None


def _build_match_expr(q: str) -> str:
    """Tokenise the user query into FTS5 phrase syntax.

    Each whitespace-delimited token becomes a quoted phrase
    (``"tok"``), which FTS5 treats as a literal substring (no
    operator interpretation). Embedded ``"`` chars are stripped so
    a user typing unbalanced quotes can't break the phrase.

    Returns an empty string when no usable token was found —
    callers short-circuit on that.
    """
    parts: list[str] = []
    for tok in q.strip().split():
        clean = tok.replace('"', "").strip()
        if clean:
            parts.append(f'"{clean}"')
    return " ".join(parts)


def search_chat_history(
    *,
    uid: int,
    q: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[SearchHit], int]:
    """Run a single FTS5 query and return ``(hits, total)``.

    ``uid`` is the **cross-platform scope key**: results include
    every session row whose ``chat_sessions.uid`` matches,
    regardless of ``channel`` (webui / tg / future IMs) or the
    row's ``delivery_address``.

    ``q`` may be empty / whitespace-only; returns ``([], 0)``
    without touching the DB.

    Raises :class:`SearchUnavailable` if FTS5 isn't compiled into
    this SQLite.
    """
    if not q or not q.strip():
        return [], 0

    if not _chat_search_available():
        raise SearchUnavailable(
            "Full-text search is not available in this build "
            "(SQLite FTS5 missing)"
        )

    match_expr = _build_match_expr(q)
    if not match_expr:
        return [], 0

    base_sql = """
        FROM chat_messages_fts
        JOIN chat_messages m ON m.id = chat_messages_fts.rowid
        JOIN chat_sessions s  ON s.session_id = m.session_id
        WHERE chat_messages_fts MATCH :match_expr
          AND s.uid = :uid"""
    count_sql = "SELECT COUNT(*) " + base_sql
    page_sql = (
        "SELECT m.session_id, m.message_id, m.role, m.ts, "
        "       s.title, s.channel, s.delivery_address, "
        "       snippet(chat_messages_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet, "
        "       bm25(chat_messages_fts) AS score "
        + base_sql +
        " ORDER BY score LIMIT :limit OFFSET :offset"
    )

    with open_session() as db:
        total = db.execute(
            text(count_sql),
            {"match_expr": match_expr, "uid": uid},
        ).scalar_one()
        rows = db.execute(
            text(page_sql),
            {
                "match_expr": match_expr,
                "uid": uid,
                "limit": limit,
                "offset": offset,
            },
        ).fetchall()

    hits = [
        SearchHit(
            session_id=r.session_id,
            message_id=r.message_id,
            role=r.role,
            ts=r.ts,
            snippet=r.snippet,
            title=r.title,
            score=float(r.score),
            delivery_address=r.delivery_address,
            channel=r.channel,
        )
        for r in rows
    ]
    return hits, total


__all__ = [
    "SearchHit",
    "SearchUnavailable",
    "search_chat_history",
]