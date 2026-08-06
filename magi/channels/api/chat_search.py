"""HTTP wrapper around the chat-history FTS5 search.

This module is the FastAPI surface; the actual query lives in
:class:`magi.bus.jobs.services.session.SessionService`. Keeping the HTTP wrapper
thin (admin gate, Pydantic response, error mapping) means the
agent tool can call the same query without going through
``channels.webui.api.*`` — closing the package-boundary violation
that design §18 forbids.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from magi.bus import get_bus
from magi.bus.jobs.protocols.session import SearchHit, SearchUnavailable
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.chat_sessions import SessionServiceDep, _admin_uid
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.chat_search")

router = APIRouter(tags=["chat_search"])


class SearchResponse(BaseModel):
    """``GET /api/chat/search`` response shape."""

    q: str
    uid: int
    items: list[SearchHit]
    total: int
    limit: int
    offset: int


@router.get("/chat/search", response_model=SearchResponse)
def search_chat(
    request: Request,
    _admin: AdminGate,
    _service: SessionServiceDep,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    """Full-text search across the operator's sessions.

    Scope: cross-platform via the calling contact's row id.
    AdminGate proves "is an admin"; ``_admin_uid`` resolves the
    cookie's uid to the matching Contact row; the SQL clause
    ``WHERE s.uid = :uid`` picks up every session this contact
    owns — webui, TG, or any future channel.
    """
    uid = _admin_uid(request)

    try:
        items, total = get_bus().session.search(uid, q, limit=limit, offset=offset)
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
