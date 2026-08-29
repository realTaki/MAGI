"""Action Items — the operator-facing "things to do" inbox.

A small surface that surfaces a list of to-dos in the
dashboard's Action Items sidebar pane. Each row carries a
human-readable ``title`` / ``description`` / ``target_url`` /
``priority`` / ``due_date`` set plus a ``source`` tag (``system``
or ``user``) recording the provenance of the write. The
dashboard renders those columns straight to the screen — no
payload blob, no kind-specific column.

Created by proactive system policies and user-facing tools through the same
local Book.

Dismissed / completed by the operator via the
``POST /api/action_items/{id}/complete`` endpoint below.
Auto-completion is deliberately out of scope: the operator may
want to close a row for reasons unrelated to the underlying
state ("I never chat from that account"), and forcing the row
to flip automatically on a state change would erase that
distinction.

Helpers
=======

The bus service owns creation and completion transactions.  Onboarding asks
it to ensure the per-admin credentials reminder, so the WebUI router never
opens a persistence session.

Indexes used
============

- ``ix_action_items_contact_id``  : every GET filters here.
- ``ix_action_items_contact_recent``: the (contact_id,
  created_at DESC) ordering in the open + last-7-days list.
- A unique partial index over the open reminder rows keyed
  on ``(contact_id, title)``, used by the bus as the idempotency
  guard so the same admin doesn't get two reminders
  before the first one is closed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from channels.api.auth_gates import AdminGate
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.action_items")

router = APIRouter(tags=["action_items"])


# -- response / request shapes --------------------------------------------


def _serialize(a) -> ActionItemOut:
    return ActionItemOut(
        id=a.id,
        contact_id=a.contact_id,
        title=a.title,
        description=a.description,
        target_url=a.target_url,
        priority=a.priority,
        due_date=a.due_date,
        source=a.source,
        created_at=a.created_at,
        completed_at=a.completed_at,
        completion_note=a.completion_note,
        dismissed=a.dismissed,
    )


class ActionItemOut(BaseModel):
    id: int
    contact_id: int | None
    title: str
    description: str | None = None
    target_url: str | None = None
    priority: str = "normal"
    due_date: datetime | None = None
    source: str = "system"
    created_at: datetime
    completed_at: datetime | None = None
    completion_note: str | None = None
    dismissed: bool = False


class ActionItemListOut(BaseModel):
    """The GET response. ``server_time`` lets the frontend
    render "3h ago" without trusting the client clock — useful
    even at v0 because the chat pane runs on the same host and
    clock skew is rare but not impossible."""

    items: list[ActionItemOut]
    server_time: datetime


class ActionItemCompleteRequest(BaseModel):
    """Optional body for the ``complete`` endpoint. ``None`` or
    empty string means "the operator didn't leave a note"."""

    completion_note: str | None = Field(default=None, max_length=500)


# -- routes -----------------------------------------------------------------


# Default window: completed rows newer than this still show
# under "最近完成". 7 days strikes a balance between "useful
# recent history" and "ancient noise". The dashboard's
# "最近完成" disclosure caps at this cut-off so very old
# rows don't render. Operators wanting a longer history
# can query ``/api/chat/conversations`` (D.6) for the full
# conversation list.
_COMPLETED_VISIBLE_DAYS = 7


def _current_admin_id(_admin: str) -> int:
    """Reuse the AdminGate-resolved admin contact_id.

    ``AdminGate`` already validated the cookie (or the
    control-plane proxy signature) and confirmed the
    resolved local Contact row is ``admin=True``. We
    re-parse the int here so the rest of the file keeps
    a single ``admin_id: int`` shape. Re-deriving the
    caller from the raw ``magi_session`` cookie used to
    live here, but that broke the proxied/control-plane
    session shape (the v2 ``v2.<payload>.<sig>`` cookie
    can't be parsed by the legacy single-contact_id helper)
    and silently failed every admin action even after a
    successful sign-in.
    """
    try:
        return int(_admin)
    except (TypeError, ValueError) as exc:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no admin contact row bound to this session",
        ) from exc


@router.get("/action_items", response_model=ActionItemListOut)
def list_action_items(
    _admin: AdminGate,
    bus: BusDep,
    include_completed: bool = True,
) -> ActionItemListOut:
    """List the caller's action items.

    ``include_completed`` (default true) controls whether
    rows completed within the last 7 days appear alongside
    open rows. The dashboard mixes them in the same
    scroll, so the default fits the typical panel.

    Only items whose ``contact_id`` matches the current
    admin are returned. The endpoint resolves the admin id
    from the session cookie — never from a query parameter —
    so the URL has no "look at someone else's items"
    affordance.
    """
    admin_id = _current_admin_id(_admin)

    # Open rows: always returned. A row with completed_at set
    # within the window OR dismissed within the window are
    # also returned iff ``include_completed`` is on. Order:
    # open before completed (cast completed_at IS NOT NULL as
    # 0), priority DESC ("high" > "normal" via alpha compare
    # which is enough for v0), then most-recent first.
    rows = bus.action_items_book.list_actions(
        owner_contact_id=admin_id,
        include_completed=include_completed,
        completed_visible_days=_COMPLETED_VISIBLE_DAYS,
    )
    return ActionItemListOut(
        items=[_serialize(r) for r in rows],
        server_time=datetime.now(UTC).replace(tzinfo=None),
    )


@router.post("/action_items/{item_id}/complete", response_model=ActionItemOut)
def complete_action_item(
    item_id: int,
    payload: ActionItemCompleteRequest,
    _admin: AdminGate,
    bus: BusDep,
) -> ActionItemOut:
    """Mark an item complete. Idempotent.

    Re-clicking "完成" on an already-completed row returns
    200 with the existing state — second call does *not*
    refresh ``completed_at`` so the timestamp records the
    first action, not the last. Concurrent calls are safe
    under SQLite's WAL; a future Postgres move inherits the
    same idempotency from the "first writer wins on
    completed_at" check.

    Authorization is doubled: the AdminGate proves the cookie
    is admin + alive, and we additionally verify the row's
    ``contact_id`` belongs to this admin. The second check
    defends against a future bug where some code path mints a
    row tied to a different contact_id and the operator
    could complete someone else's item via URL guessing.
    """
    admin_id = _current_admin_id(_admin)
    service = bus.action_items_book
    row = service.get(item_id)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.action_item",
            detail=f"action item {item_id} not found",
        )
    if row.contact_id != admin_id:
        logger.warning(
            "complete denied: admin=%s tried to complete item %s owned by %s",
            admin_id,
            item_id,
            row.contact_id,
        )
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.not_your_action_item",
            detail="this action item is owned by another operator",
        )

    row = service.complete(
        action_item_id=item_id,
        note=(payload.completion_note if "completion_note" in payload.model_fields_set else None),
    )
    if row is None:  # Ownership was rechecked inside the bus transaction.
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.not_your_action_item",
            detail="this action item is owned by another operator",
        )
    logger.info(
        "action item completed (id=%s, source=%s, admin=%s)",
        row.id,
        row.source,
        admin_id,
    )
    return _serialize(row)
