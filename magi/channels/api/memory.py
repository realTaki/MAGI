"""``GET /api/memory`` — read-only MAGI memory surface for
the Knowledge → Memory pane.

Scope: every ``MemoryEntry`` row owned by the calling
admin (``MemoryEntry.contact_id == admin_contact_id``).
The pane renders the operator's view of "what the LLM
knows" — both kinds, in-flight + completed, ordered by
priority DESC then updated_at DESC (the same ordering
the system-prompt formatter uses, so what the LLM sees
and what the operator sees stay in sync).

v0 deliberately does NOT expose edit / delete endpoints:

  - ``add_memory`` / ``update_memory`` / ``complete_memory``
    / ``delete_memory`` are LLM tools already.
  - Direct operator-driven add/edit/complete is a C4+
    affordance (the contact store mirrors this — same
    reasoning, same shape).

What the operator gets here:

  - **Subject** (the row's title; rendered as the table
    cell's primary text).
  - **Kind** (``fact`` / ``quick_note``) — distinct from
    contacts; ``quick_note`` rows have a completion state
    that the UI shows as a small "已完成 · YYYY-MM-DD"
    suffix on the row.
  - **Priority** (1-5) — the same score the LLM uses
    to prioritise the system-prompt block.
  - **Updated at** — when the LLM last touched the row.
  - **Body preview** (200 chars) — the markdown body in
    full, as ``title=`` tooltip on hover. The store
    caps body at 8 KB so the preview represents most
    rows verbatim; the cap kicks in only on the largest
    ones.

The endpoint intentionally does NOT pre-filter completed
``quick_note`` rows like the system-prompt formatter does —
the operator view is the audit trail; the formatter view
is the LLM's working set. Different purposes, different
filtering.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.chat_conversations import _admin_contact_id
from magi.channels.api.dependencies import BusDep

logger = logging.getLogger("magi.api.memory")

router = APIRouter(tags=["memory"])


# Cap on rows returned. A single admin's memory table is
# operator-curated; 200 is a comfortable working set (and
# matches the contacts endpoint's cap so the two panes
# share a paging contract). The store's default for
# ``list_for_owner`` is 50; we bump to 200 because the
# WebUI is the audit view, not the LLM's working set.
_MAX_ROWS = 200


# -- response shapes -------------------------------------------------------


class MemoryOut(BaseModel):
    id: int
    # ``kind`` is exposed verbatim — the UI renders a
    # localised badge ("事实" / "快速笔记") via i18n keys.
    kind: str
    subject: str
    body: str
    priority: int
    # ``completed_at`` is null for fact rows (they
    # never expire) and for in-flight quick_note rows; set
    # for completed quick_note rows. The UI uses this to
    # render the "已完成 · YYYY-MM-DD" suffix.
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListOut(BaseModel):
    items: list[MemoryOut]
    total: int


# -- helpers ---------------------------------------------------------------


def _serialize(row) -> MemoryOut:
    return MemoryOut(
        id=row.id,
        kind=row.kind,
        subject=row.subject,
        body=row.body,
        priority=row.priority,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# -- routes ----------------------------------------------------------------


@router.get("/memory", response_model=MemoryListOut)
def list_memory(
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> MemoryListOut:
    """Enumerate the calling admin's memory rows.

    Auth is doubled: ``AdminGate`` proves the cookie is a
    live admin session, and ``_current_admin_id`` re-reads
    the cookie to get the int ``contact_id`` that scopes
    the query. Defends against a future bug where some
    code path mints a row tied to a different contact and
    the operator could read someone else's memory via URL
    guessing.

    Ordering is whatever ``MemoryBook.list_by_owner`` returns
    (``created_at DESC``) — the same call, and therefore the same
    order, the system-prompt memory block is built from, so what
    the LLM sees lines up with what the operator sees in the
    dashboard. Both ``fact`` and ``quick_note`` kinds are included
    regardless of completion state — the operator view is the audit
    trail.
    """
    admin_id = _admin_contact_id(request)
    # ``MemoryBook.list_by_owner`` is keyword-only and takes ``contact_id``
    # alone — it already returns every row for the owner (both kinds,
    # completed included), so the cap is applied here rather than
    # pushed into the query.
    rows = bus.memory_book.list_by_owner(contact_id=admin_id)[:_MAX_ROWS]
    return MemoryListOut(
        items=[_serialize(r) for r in rows],
        total=len(rows),
    )
