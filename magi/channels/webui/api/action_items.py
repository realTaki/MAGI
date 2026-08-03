"""Action Items — the operator-facing "things to do" inbox.

A small surface that surfaces a list of to-dos in the
dashboard's Action Items sidebar pane. Each row is keyed on a
stable ``kind`` string ("llm_credentials_missing" today;
``eve_followup_*`` kinds land later when C4 ships) and carries
human-readable ``title`` / ``description`` / ``target_url``
columns. The dashboard renders the columns straight to the
screen — no payload blob, no kind-specific column.

Created by system paths (currently ``onboarding/complete``
inserts one ``llm_credentials_missing`` row per admin). From
C4, EVE-driven rows land via a future ``POST /api/action_items``
endpoint — schema already accommodates them (``source='eve'``,
``priority='high'``).

Dismissed / completed by the operator via the
``POST /api/action_items/{id}/complete`` endpoint below.
Auto-completion is deliberately out of scope: the operator may
want to close a row for reasons unrelated to the underlying
state ("I never chat from that account"), and forcing the row
to flip automatically on a state change would erase that
distinction.

Helpers
=======

The BUS service owns creation and completion transactions.  Onboarding asks
it to ensure the per-admin credentials reminder, so the WebUI router never
opens a persistence session.

Indexes used
============

- ``ix_action_items_uid``  : every GET filters here.
- ``ix_action_items_contact_recent``: the (uid,
  created_at DESC) ordering in the open + last-7-days list.
- ``ux_action_items_open_per_kind``: BUS-side idempotency guard for the
  onboarding credentials reminder.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.bus import bootstrap
from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.constants import STATE_DIR

logger = logging.getLogger("magi.api.action_items")

router = APIRouter(tags=["action_items"])


# -- response / request shapes --------------------------------------------


def _serialize(a) -> "ActionItemOut":
    return ActionItemOut(
        id=a.id,
        uid=a.uid,
        kind=a.kind,
        title=a.title,
        description=a.description,
        target_url=a.target_url,
        priority=a.priority,
        due_date=_iso(a.due_date),
        source=a.source,
        created_at=_iso(a.created_at) or "",
        completed_at=_iso(a.completed_at),
        completed_by_uid=a.completed_by_uid,
        completion_note=a.completion_note,
        dismissed=a.dismissed,
    )


class ActionItemOut(BaseModel):
    id: int
    uid: int | None
    kind: str
    title: str
    description: str | None = None
    target_url: str | None = None
    priority: str = "normal"
    due_date: str | None = None
    source: str = "system"
    created_at: str
    completed_at: str | None = None
    completed_by_uid: int | None = None
    completion_note: str | None = None
    dismissed: bool = False


class ActionItemListOut(BaseModel):
    """The GET response. ``server_time`` lets the frontend
    render "3h ago" without trusting the client clock — useful
    even at v0 because the chat pane runs on the same host and
    clock skew is rare but not impossible."""

    items: list[ActionItemOut]
    server_time: str


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
# can query ``/api/chat/sessions`` (D.6) for the full
# session list.
_COMPLETED_VISIBLE_DAYS = 7


def _bus():
    return bootstrap(os.environ.get("MAGI_STATE_DIR", STATE_DIR))


def _current_admin_id(request: Request) -> int:
    """Resolve the cookie's admin Contact id.

    ``AdminGate`` already validated cookie + admin row
    membership, so under normal flow this always returns an
    int. The defensive re-check mirrors
    :func:`magi.channels.webui.api.chat._resolve_caller_credentials`:
    if a future caller bypasses the gate, this still fails
    closed with a ``chat.unknown_sender`` 401 — the same
    code as chat.py, so the frontend's friendly
    "登录失效了" message handles both endpoints.

    D.24: cookie carries ``contact.id`` (an int). Lookup
    is by primary key, not by ``telegram_id`` — that
    matched the pre-D.24 cookie (which carried a TG
    chat id), but with the contact-id cookie the
    ``Contact.telegram_id == cid_int`` query only
    matches by sheer coincidence.
    """
    from magi.channels.webui.api.auth import _verify_signed_uid
    raw = request.cookies.get("magi_session") or ""
    uid = _verify_signed_uid(raw)
    if uid is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no admin contact row bound to this session",
        )
    contact = _bus().contacts.get(uid)
    if contact is None or not contact.admin:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no admin contact row bound to this session",
        )
    return contact.id


@router.get("/action_items", response_model=ActionItemListOut)
def list_action_items(
    request: Request,
    _admin: AdminGate,
    include_completed: bool = True,
    kind: str | None = None,
) -> ActionItemListOut:
    """List the caller's action items.

    - ``include_completed`` (default true) controls whether
      rows completed within the last 7 days appear alongside
      open rows. The dashboard mixes them in the same
      scroll, so the default fits the typical panel.
    - ``kind`` narrows by the stable kind code
      (``llm_credentials_missing``, future ``eve_*``).

    Only items whose ``uid`` matches the current
    admin are returned. The endpoint resolves the admin id
    from the session cookie — never from a query parameter —
    so the URL has no "look at someone else's items"
    affordance.
    """
    admin_id = _current_admin_id(request)

    # Open rows: always returned. A row with completed_at set
    # within the window OR dismissed within the window are
    # also returned iff ``include_completed`` is on. Order:
    # open before completed (cast completed_at IS NOT NULL as
    # 0), priority DESC ("high" > "normal" via alpha compare
    # which is enough for v0), then most-recent first.
    rows = _bus().action_item.list_for_owner(
        owner_uid=admin_id,
        include_completed=include_completed,
        kind=kind,
        completed_visible_days=_COMPLETED_VISIBLE_DAYS,
    )
    return ActionItemListOut(
        items=[_serialize(r) for r in rows],
        server_time=datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    )


@router.post(
    "/action_items/{item_id}/complete", response_model=ActionItemOut
)
def complete_action_item(
    item_id: int,
    payload: ActionItemCompleteRequest,
    request: Request,
    _admin: AdminGate,
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
    ``uid`` belongs to this admin. The second check
    defends against a future bug where some code path mints a
    row tied to a different uid and the operator
    could complete someone else's item via URL guessing.
    """
    admin_id = _current_admin_id(request)
    service = _bus().action_item
    row = service.get(item_id)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.action_item",
            detail=f"action item {item_id} not found",
        )
    if row.uid != admin_id:
        logger.warning(
            "complete denied: admin=%s tried to complete item %s owned by %s",
            admin_id, item_id, row.uid,
        )
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.not_your_action_item",
            detail="this action item is owned by another operator",
        )

    row = service.complete_for_owner(
        action_item_id=item_id,
        owner_uid=admin_id,
        note=(payload.completion_note if "completion_note" in payload.model_fields_set else None),
    )
    if row is None:  # Ownership was rechecked inside the BUS transaction.
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.not_your_action_item",
            detail="this action item is owned by another operator",
        )
    logger.info(
        "action item completed (id=%s, kind=%s, admin=%s)",
        row.id, row.kind, admin_id,
    )
    return _serialize(row)
