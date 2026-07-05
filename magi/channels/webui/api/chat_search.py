"""Full-text search across chat sessions — D.18.

Searches every message (active + archived, in any session
belonging to the caller's ``chat_id``) using the FTS5
virtual table maintained by triggers on ``chat_messages``.
Results are scoped to the calling admin's ``chat_id`` via
the SQL join on ``chat_sessions.chat_id`` — a raw FTS
``MATCH`` would otherwise return rows from other operators
in the same DB.

Why scope at SQL (not filesystem)
---------------------------------

Pre-D.18 the directory layout ``<chat_id>/<sid>.json``
enforced per-operator isolation for free. With sessions in
SQLite, the WHERE clause is the new boundary. The auth gate
(``AdminGate``) only proves "is an admin"; the chat_id
binding is what scopes data. Both must be present on every
read.

Query sanitisation
------------------

FTS5 query syntax treats ``"``, ``*``, ``(``, ``)``, ``:``,
``AND``/``OR``/``NOT``, ``^``, ``-`` specially. User input
is split on whitespace and each token is wrapped in
``"…"`` (phrase form) so the operators above can't trigger
a syntax error or change matching semantics. Empty queries
short-circuit to ``[]`` without touching the DB.

The token wrapper still escapes embedded ``"`` chars so a
user typing ``she said "hello"`` doesn't break the phrase
quoting.

Degraded mode
-------------

If FTS5 isn't compiled into the SQLite the project ships
with (rare on CPython 3.12+, but possible on stripped
distros), the route returns ``503 search.unavailable``
instead of crashing. The boot-time probe in
:mod:`magi.runtime.state.orm` skips the FTS DDL on a no-FTS
SQLite, so this endpoint just has to detect "no virtual
table" and respond accordingly.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from magi.channels.webui.api.chat_sessions import _resolve_chat_id
from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.runtime.state.orm import open_session

logger = logging.getLogger("magi.api.chat_search")

router = APIRouter(tags=["chat_search"])


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


# -- Pydantic shapes --------------------------------------------------------


class SearchHit(BaseModel):
    session_id: str
    message_id: str
    role: str
    ts: str
    # Snippet text with <mark>…</mark> wrappers around the
    # matched substring. Up to ~16 tokens on either side of
    # the match; the FTS5 ``snippet()`` function decides the
    # exact window.
    snippet: str
    # The session's title at the time of the search. ``null``
    # when no auto-title / manual title has been set yet.
    title: str | None
    # bm25() score — lower is more relevant. Surfaced for
    # debugging; the frontend doesn't sort on it (FTS
    # already returns ranked).
    score: float


class SearchResponse(BaseModel):
    q: str
    chat_id: str
    items: list[SearchHit]
    total: int
    limit: int
    offset: int


# -- helpers ----------------------------------------------------------------


def _chat_search_available() -> bool:
    """Probe whether the FTS5 virtual table exists.

    Cheap (one ``sqlite_master`` row lookup). Runs on every
    search request; the alternative would be a module-level
    cache that could lie after a schema rebuild, so the
    freshness is worth the microsecond.
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
    (``"tok"``), which FTS5 treats as a literal substring
    (no operator interpretation). Embedded ``"`` chars are
    stripped so a user typing unbalanced quotes can't break
    the phrase.

    Returns an empty string when no usable token was found
    — callers short-circuit on that.
    """
    parts: list[str] = []
    for tok in q.strip().split():
        # Drop whitespace-only tokens (defensive — strip()
        # on the outer string already did this) and any
        # quotes that would unbalance the phrase.
        clean = tok.replace('"', "").strip()
        if clean:
            parts.append(f'"{clean}"')
    return " ".join(parts)


# -- route ------------------------------------------------------------------


@router.get("/chat/search", response_model=SearchResponse)
def search_chat(
    request: Request,
    _admin: AdminGate,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    """Full-text search across the operator's sessions.

    The caller is identified by the ``magi_session`` cookie.
    The result set is scoped to sessions whose
    ``chat_sessions.chat_id`` matches the cookie — other
    operators' rows are never reachable from this endpoint
    even if a query happened to match.
    """
    chat_id_int = _resolve_chat_id(request)
    chat_id = str(chat_id_int)

    # 1. Empty / whitespace query → empty result, no DB hit.
    if not q or not q.strip():
        return SearchResponse(
            q=q, chat_id=chat_id,
            items=[], total=0, limit=limit, offset=offset,
        )

    # 2. FTS5 not built into this SQLite → degrade gracefully.
    if not _chat_search_available():
        raise MagiHTTPException(
            status_code=503,
            code="search.unavailable",
            detail=(
                "Full-text search is not available in this build "
                "(SQLite FTS5 missing)"
            ),
        )

    # 3. Sanitise the query into FTS5 phrase syntax.
    match_expr = _build_match_expr(q)
    if not match_expr:
        return SearchResponse(
            q=q, chat_id=chat_id,
            items=[], total=0, limit=limit, offset=offset,
        )

    # 4. Run the search. The ``JOIN chat_sessions`` is the
    # per-operator scope — the FTS rowid alone is not
    # enough to filter to one chat_id.
    base_sql = """
        FROM chat_messages_fts
        JOIN chat_messages m ON m.id = chat_messages_fts.rowid
        JOIN chat_sessions s  ON s.session_id = m.session_id
        WHERE chat_messages_fts MATCH :match_expr
          AND s.chat_id = :chat_id
    """
    count_sql = "SELECT COUNT(*) " + base_sql
    page_sql = (
        "SELECT m.session_id, m.message_id, m.role, m.ts, "
        "       s.title, "
        "       snippet(chat_messages_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet, "
        "       bm25(chat_messages_fts) AS score "
        + base_sql +
        " ORDER BY score LIMIT :limit OFFSET :offset"
    )

    with open_session() as db:
        try:
            total = db.execute(
                text(count_sql),
                {"match_expr": match_expr, "chat_id": chat_id},
            ).scalar_one()
            rows = db.execute(
                text(page_sql),
                {
                    "match_expr": match_expr,
                    "chat_id": chat_id,
                    "limit": limit,
                    "offset": offset,
                },
            ).fetchall()
        except Exception as e:
            # SQLite raises on a syntactically invalid MATCH
            # expression. With the per-token phrase wrapper
            # this should never happen, but defend against
            # FTS5-specific surprises (e.g. empty token after
            # quote stripping in a corner case we missed).
            logger.warning("chat search rejected: %s", e)
            raise MagiHTTPException(
                status_code=400,
                code="search.bad_query",
                detail=str(e),
            )

        items = [
            SearchHit(
                session_id=r.session_id,
                message_id=r.message_id,
                role=r.role,
                ts=r.ts,
                snippet=r.snippet,
                title=r.title,
                score=float(r.score),
            )
            for r in rows
        ]

    return SearchResponse(
        q=q, chat_id=chat_id,
        items=items, total=total,
        limit=limit, offset=offset,
    )