"""Full-text search across chat sessions — D.18.

Searches every message (active + archived, in any session
belonging to the caller) using the FTS5 virtual table
maintained by triggers on ``chat_messages``.

Scope: per-employee
-------------------

The scope is the calling employee's own sessions, identified
by ``Employee.telegram_id`` (the cookie value). Pre-D.18
the directory layout ``<tgid>/<sid>.json`` enforced
per-operator isolation for free; with sessions in SQLite
the WHERE clause is the new boundary. Two parallel helpers
exist:

  - ``_resolve_chat_id`` — returns the cookie value as an
    ``int``. Used where the data column (``chat_sessions.
    tgid``) is what matters.
  - ``_admin_uid`` — returns the ``Employee.id``
    (PK) of the admin. Used where the data column is
    ``Employee.telegram_id`` (a FK), or where we want to
    operate on the row rather than the chat identifier.

The D.18 search endpoint scopes by ``chat_sessions.tgid``
which **is** the telegram_id (just stored as a string).
Semantically the data still belongs to one employee, but
the column key is ``tgid`` not ``uid``. The
``search_sessions`` tool (D.18+1) takes a slightly different
approach: it also takes the calling employee as the scope,
but exposes the **employee identity** rather than the chat
identifier, so an LLM working on behalf of one employee
doesn't accidentally think of itself as "scoped to a chat".

Reused by the ``search_sessions`` tool
---------------------------------------

The ``search_chat_history()`` function below is the
implementation behind both the HTTP route and the agent
tool. The tool calls it directly (no HTTP round-trip); the
HTTP route wraps the result in a Pydantic shape for the
frontend. Sharing the function keeps query sanitisation +
FTS5 availability + tgid scope in one place.

Query sanitisation
------------------

FTS5 query syntax treats ``"``, ``*``, ``(``, ``)``, ``:``,
``AND``/``OR``/``NOT``, ``^``, ``-`` specially. User input
is split on whitespace and each token is wrapped in
``"…"`` (phrase form) so the operators above can't trigger
a syntax error or change matching semantics. Empty queries
short-circuit to ``[]`` without touching the DB.

Degraded mode
-------------

If FTS5 isn't compiled into the SQLite the project ships
with (rare on CPython 3.12+, but possible on stripped
distros), the route returns ``503 search.unavailable``
instead of crashing. The boot-time probe in
:mod:`magi.agent.db.orm` skips the FTS DDL on a no-FTS
SQLite, so this endpoint just has to detect "no virtual
table" and respond accordingly.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from magi.channels.webui.api.chat_sessions import _admin_uid, SessionStoreDep
from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.agent.db import open_session

logger = logging.getLogger("magi.api.chat_search")

router = APIRouter(tags=["chat_search"])


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
    # D.18+1: the row's ``tgid`` (Telegram chat identifier).
    # Surfaced so cross-platform callers can see which
    # channel/identity the hit landed in; the agent tool
    # also uses it to fetch the surrounding messages with
    # the right scope guard.
    tgid: str
    channel: str


class SearchResponse(BaseModel):
    q: str
    # The employee's row id whose history was searched
    # (cross-platform scope: matches every session row
    # whose ``uid`` equals this, regardless of
    # ``channel`` / ``tgid``).
    uid: int
    items: list[SearchHit]
    total: int
    limit: int
    offset: int


# -- shared helpers ---------------------------------------------------------


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


class SearchUnavailable(Exception):
    """Raised by :func:`search_chat_history` when FTS5 isn't
    built into the SQLite the project is running against.

    The HTTP route translates this to ``503 search.unavailable``;
    the agent tool returns the message text in a
    ``ToolResult(is_error=True, ...)`` so the LLM sees the
    same hint.
    """


def search_chat_history(
    *,
    uid: int,
    q: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[SearchHit], int]:
    """Run a single FTS5 query and return ``(hits, total)``.

    ``uid`` is the **cross-platform scope key**:
    results include every session row whose
    ``chat_sessions.uid`` matches, regardless of
    ``channel`` (webui / tg / future IMs) or ``tgid``. This
    matches the user's "search all of one employee's
    history" intent — an admin who has both webui
    conversations and TG conversations under the same
    employee row should see them all in one search.

    ``q`` may be empty / whitespace-only; returns
    ``([], 0)`` without touching the DB.

    Raises :class:`SearchUnavailable` if FTS5 isn't
    compiled into this SQLite — both the HTTP route and
    the agent tool handle that case distinctly (503 vs an
    error ToolResult).
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
          AND s.uid = :uid
    """
    count_sql = "SELECT COUNT(*) " + base_sql
    page_sql = (
        "SELECT m.session_id, m.message_id, m.role, m.ts, "
        "       s.title, s.channel, s.tgid, "
        "       snippet(chat_messages_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet, "
        "       bm25(chat_messages_fts) AS score "
        + base_sql +
        " ORDER BY score LIMIT :limit OFFSET :offset"
    )

    with open_session() as db:
        try:
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
        except Exception as e:
            logger.warning("chat search rejected: %s", e)
            raise

        hits = [
            SearchHit(
                session_id=r.session_id,
                message_id=r.message_id,
                role=r.role,
                ts=r.ts,
                snippet=r.snippet,
                title=r.title,
                score=float(r.score),
                tgid=r.tgid,
                channel=r.channel,
            )
            for r in rows
        ]
        return hits, total


# -- HTTP route -------------------------------------------------------------


@router.get("/chat/search", response_model=SearchResponse)
def search_chat(
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    """Full-text search across the operator's sessions.

    Scope: cross-platform via the calling employee's row
    id. AdminGate proves "is an admin"; ``_admin_uid``
    resolves the cookie's tgid to the matching Employee
    row (FK to ``Employee.telegram_id``); the SQL clause
    ``WHERE s.uid = :uid`` then picks up
    every session this employee owns — webui, TG, or any
    future channel. Other employees' rows are never
    reachable.
    """
    uid = _admin_uid(request, store)

    try:
        items, total = search_chat_history(
            uid=uid, q=q, limit=limit, offset=offset,
        )
    except SearchUnavailable as e:
        raise MagiHTTPException(
            status_code=503,
            code="search.unavailable",
            detail=str(e),
        )

    return SearchResponse(
        q=q, uid=uid,
        items=items, total=total,
        limit=limit, offset=offset,
    )