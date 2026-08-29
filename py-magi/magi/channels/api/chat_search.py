"""HTTP wrapper around the chat-history FTS5 search.

This module is the FastAPI surface; bus owns the actual query. Keeping the HTTP
wrapper thin (admin gate, Pydantic response, error mapping) means the
agent tool can call the same query without going through
``channels.webui.api.*`` — closing the package-boundary violation
that design §18 forbids.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from magi.old_bus.firmwares.books.local.conversationBook import (
    SearchHit,
    SearchUnavailable,
)
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.chat_conversations import _admin_contact_id
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.chat_search")

router = APIRouter(tags=["chat_search"])


class SearchResponse(BaseModel):
    """``GET /api/chat/search`` response shape."""

    q: str
    contact_id: int
    items: list[SearchHit]
    total: int
    limit: int
    offset: int


@router.get("/chat/search", response_model=SearchResponse)
def search_chat(
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    """Full-text search across the operator's conversations.

    Scope: cross-platform via the calling contact's row id.
    AdminGate proves "is an admin"; ``_admin_contact_id`` resolves the
    cookie's contact_id to the matching Contact row; the SQL clause
    ``WHERE s.contact_id = :contact_id`` picks up every conversation this
    contact owns — webui, TG, or any future channel.
    """
    contact_id = _admin_contact_id(request)

    try:
        items, total = bus.messages_book.search(
            contact_id=contact_id, q=q, limit=limit, offset=offset
        )
    except SearchUnavailable as e:
        raise MagiHTTPException(  # noqa: B904
            status_code=503,
            code="search.unavailable",
            detail=str(e),
        )

    return SearchResponse(
        q=q,
        contact_id=contact_id,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
