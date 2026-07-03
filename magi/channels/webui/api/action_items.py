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

``_ensure_llm_credentials_item(session, employee_id)`` lives
in this module too so :mod:`onboarding` can call it
uncommitted (the surrounding ``onboarding/complete`` body
commits in one shot). The helper is idempotent: ``SELECT 1
... WHERE completed_at IS NULL AND dismissed = 0`` is a no-op
if any open row for the same ``(employee_id, kind)`` already
exists, and the partial unique index at ``ux_action_items_open_per_kind``
backs that up.

Indexes used
============

- ``ix_action_items_employee_id``  : every GET filters here.
- ``ix_action_items_employee_recent``: the (employee_id,
  created_at DESC) ordering in the open + last-7-days list.
- ``ux_action_items_open_per_kind``: idempotency check in
  ``_ensure_llm_credentials_item``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.runtime.state.orm import ActionItem, Employee, get_session

logger = logging.getLogger("magi.api.action_items")

router = APIRouter(tags=["action_items"])


# -- response / request shapes --------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    """Format a datetime as ISO 8601 UTC, or None.

    Keeps "completed_at is null" rendering simple in JS without
    forcing the renderer to import a date library.
    """
    if dt is None:
        return None
    # Treat naive datetimes as UTC — they were created via
    # ``datetime.utcnow()`` which is the project convention.
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _serialize(a: ActionItem) -> "ActionItemOut":
    return ActionItemOut(
        id=a.id,
        employee_id=a.employee_id,
        kind=a.kind,
        title=a.title,
        description=a.description,
        target_url=a.target_url,
        priority=a.priority,
        source=a.source,
        created_at=_iso(a.created_at) or "",
        completed_at=_iso(a.completed_at),
        completed_by_employee_id=a.completed_by_employee_id,
        completion_note=a.completion_note,
        dismissed=a.dismissed,
    )


class ActionItemOut(BaseModel):
    id: int
    employee_id: int | None
    kind: str
    title: str
    description: str | None = None
    target_url: str | None = None
    priority: str = "normal"
    source: str = "system"
    created_at: str
    completed_at: str | None = None
    completed_by_employee_id: int | None = None
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


# -- helper ----------------------------------------------------------------


def _ensure_llm_credentials_item(
    session: Session, employee_id: int
) -> bool:
    """Create an ``llm_credentials_missing`` row for the employee
    if no open one already exists.

    Returns ``True`` if a new row was added, ``False`` if one
    was already there (idempotent no-op). The caller is
    responsible for ``session.commit()`` — the surrounding
    ``onboarding/complete`` body has its own commit point and
    we don't want to introduce a second one that could collide
    with the partial-unique index at the wrong moment.

    Idempotency is enforced two ways:

      1. ``SELECT ... WHERE completed_at IS NULL AND
         dismissed = 0`` short-circuit (cheap).
      2. The partial unique index
         ``ux_action_items_open_per_kind`` on
         ``(employee_id, kind) WHERE completed_at IS NULL AND
         dismissed = 0`` is the safety net against a race
         between two concurrent ``complete`` calls. The
         session-level short-circuit + partial unique
         together mean an open row is created at most once
         per ``(employee_id, kind)``.
    """
    existing = session.scalar(
        select(ActionItem).where(
            ActionItem.employee_id == employee_id,
            ActionItem.kind == "llm_credentials_missing",
            ActionItem.completed_at.is_(None),
            ActionItem.dismissed.is_(False),
        )
    )
    if existing is not None:
        return False
    session.add(
        ActionItem(
            employee_id=employee_id,
            kind="llm_credentials_missing",
            title="设置你的 LLM provider 和 API key",
            description=(
                "切到「员工」tab，找到自己的档案，"
                "把 Provider 和 API Key 填上。"
            ),
            target_url="/dashboard?tab=organization",
            priority="normal",
            source="system",
        )
    )
    return True


# -- routes -----------------------------------------------------------------


# Default window: completed rows newer than this still show
# under "最近完成". 7 days strikes a balance between "useful
# recent history" and "ancient noise". The dashboard's
# "最近完成" disclosure caps at this cut-off so very old
# rows don't render — operators wanting the full audit can
# look at the audit-log view (Phase 2).
_COMPLETED_VISIBLE_DAYS = 7


def _current_admin_id(
    request: Request, session: Session
) -> int:
    """Resolve the cookie's admin Employee id.

    ``AdminGate`` already validated cookie + admin row
    membership, so under normal flow this always returns an
    int. The defensive re-check mirrors
    :func:`magi.channels.webui.api.chat._resolve_caller_credentials`:
    if a future caller bypasses the gate, this still fails
    closed with a ``chat.unknown_sender`` 401 — the same
    code as chat.py, so the frontend's friendly
    "登录失效了" message handles both endpoints.
    """
    chat_id = request.cookies.get("magi_session") or ""
    try:
        cid_int = int(chat_id)
    except (TypeError, ValueError):
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no admin employee row bound to this chat_id",
        )
    emp = session.scalar(
        select(Employee).where(Employee.telegram_id == cid_int)
    )
    if emp is None or emp.role != "admin":
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no admin employee row bound to this chat_id",
        )
    return emp.id


@router.get("/action_items", response_model=ActionItemListOut)
def list_action_items(
    request: Request,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
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

    Only items whose ``employee_id`` matches the current
    admin are returned. The endpoint resolves the admin id
    from the session cookie — never from a query parameter —
    so the URL has no "look at someone else's items"
    affordance.
    """
    admin_id = _current_admin_id(request, session)

    # Open rows: always returned. A row with completed_at set
    # within the window OR dismissed within the window are
    # also returned iff ``include_completed`` is on. Order:
    # open before completed (cast completed_at IS NOT NULL as
    # 0), priority DESC ("high" > "normal" via alpha compare
    # which is enough for v0), then most-recent first.
    cutoff = datetime.utcnow() - timedelta(days=_COMPLETED_VISIBLE_DAYS)
    stmt = select(ActionItem).where(ActionItem.employee_id == admin_id)
    if kind is not None:
        stmt = stmt.where(ActionItem.kind == kind)
    if not include_completed:
        # Hide anything that's not open + not dismissed.
        stmt = stmt.where(
            ActionItem.completed_at.is_(None),
            ActionItem.dismissed.is_(False),
        )
    else:
        # Default: open rows, plus completed rows newer than
        # the window. Dismissed rows are hidden in the main
        # list by design (the operator chose to hide them).
        stmt = stmt.where(
            (ActionItem.completed_at.is_(None))
            | (ActionItem.completed_at >= cutoff)
        )
    stmt = stmt.order_by(
        ActionItem.completed_at.is_(None).desc(),
        ActionItem.priority.desc(),
        ActionItem.created_at.desc(),
    )

    rows = list(session.scalars(stmt).all())
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
    session: Annotated[Session, Depends(get_session)],
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
    ``employee_id`` belongs to this admin. The second check
    defends against a future bug where some code path mints a
    row tied to a different employee_id and the operator
    could complete someone else's item via URL guessing.
    """
    admin_id = _current_admin_id(request, session)
    row = session.get(ActionItem, item_id)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.action_item",
            detail=f"action item {item_id} not found",
        )
    if row.employee_id != admin_id:
        logger.warning(
            "complete denied: admin=%s tried to complete item %s owned by %s",
            admin_id, item_id, row.employee_id,
        )
        raise MagiHTTPException(
            status_code=403,
            code="forbidden.not_your_action_item",
            detail="this action item is owned by another operator",
        )

    # Already completed → idempotent return.
    if row.completed_at is not None:
        return _serialize(row)

    row.completed_at = datetime.utcnow()
    row.completed_by_employee_id = admin_id
    # Only overwrite the note if the caller actually sent
    # one. ``model_fields_set`` tells us "the field was
    # present in the request" — ``None`` and absent both
    # count as "don't change", but a sent empty string is
    # a legitimate "user removed the note" gesture we keep.
    if "completion_note" in payload.model_fields_set:
        row.completion_note = payload.completion_note

    session.commit()
    session.refresh(row)
    logger.info(
        "action item completed (id=%s, kind=%s, admin=%s)",
        row.id, row.kind, admin_id,
    )
    return _serialize(row)
