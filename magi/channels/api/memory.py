"""``GET /api/memory`` — read-only MAGI memory surface for
the Knowledge → Memory pane.

Scope: every ``MemoryEntry`` row owned by the calling
admin (``MemoryEntry.uid == admin_uid``).
The pane renders the operator's view of "what the LLM
knows" — both kinds, in-flight + completed, ordered by
importance DESC then updated_at DESC (the same ordering
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
  - **Kind** (``important`` / ``ongoing``) — distinct from
    contacts; ``ongoing`` rows have a completion state
    that the UI shows as a small "已完成 · YYYY-MM-DD"
    suffix on the row.
  - **Importance** (1-5) — the same score the LLM uses
    to prioritise the system-prompt block.
  - **Updated at** — when the LLM last touched the row.
  - **Body preview** (200 chars) — the markdown body in
    full, as ``title=`` tooltip on hover. The store
    caps body at 8 KB so the preview represents most
    rows verbatim; the cap kicks in only on the largest
    ones.

The endpoint intentionally does NOT pre-filter completed
``ongoing`` rows like the system-prompt formatter does —
the operator view is the audit trail; the formatter view
is the LLM's working set. Different purposes, different
filtering.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel

from magi.bus import get_bus
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.chat_sessions import _admin_uid

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
    # localised badge ("重要" / "进行中") via i18n keys.
    kind: str
    subject: str
    body: str
    importance: int
    source: str
    # ``completed_at`` is null for important rows (they
    # never expire) and for in-flight ongoing rows; set
    # for completed ongoing rows. The UI uses this to
    # render the "已完成 · YYYY-MM-DD" suffix.
    completed_at: str | None = None
    created_at: str
    updated_at: str


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
        importance=row.importance,
        source=row.source,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# -- routes ----------------------------------------------------------------


@router.get("/memory", response_model=MemoryListOut)
def list_memory(
    request: Request,
    _admin: AdminGate,
) -> MemoryListOut:
    """Enumerate the calling admin's memory rows.

    Auth is doubled: ``AdminGate`` proves the cookie is a
    live admin session, and ``_current_admin_id`` re-reads
    the cookie to get the int ``uid`` that scopes
    the query. Defends against a future bug where some
    code path mints a row tied to a different contact and
    the operator could read someone else's memory via URL
    guessing.

    Ordering mirrors the system-prompt formatter
    (``importance DESC, updated_at DESC``) so what the LLM
    sees in its working block lines up with what the
    operator sees in the dashboard. Both ``important``
    and ``ongoing`` kinds are included regardless of
    completion state — the operator view is the audit
    trail.
    """
    admin_id = _admin_uid(request)
    rows = get_bus().memory.list_for_owner(
        admin_id, include_completed=True, limit=_MAX_ROWS,
    )
    return MemoryListOut(
        items=[_serialize(r) for r in rows],
        total=len(rows),
    )
