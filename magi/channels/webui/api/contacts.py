"""``GET /api/contacts`` — read-only contacts surface for
the Knowledge → Contacts pane.

Scope: every contact row owned by the calling admin
(``ContactEntry.owner_id == admin_employee_id``), with the
``person`` FK JOIN'd to ``Employee`` + ``Employee.department``
so the UI can show "Bob 'Bobby' Chen — Engineering"
without a second round-trip.

v0 deliberately does NOT expose edit / delete endpoints:

  - ``add_contact`` / ``update_contact`` are LLM tools
    already (the LLM and operator can iterate on the row
    via conversation).
  - Delete is a sharp edge — one click and the row is
    gone, and the next chat won't have the prior context.
    Better to keep it LLM-tool-mediated so the LLM can
    confirm intent with the operator.

When the operator surface for these lands, it should
land alongside a "confirm" affordance, not bare DELETE
buttons. The pattern matches the project's "minimal by
default" rule — ship the read surface first, add write
affordances once we see real demand.

Note on role rendering: the row's ``role`` column is a
**snapshot** (per the model docstring: "the person's
role at the company as of this contact record's
creation"). It does NOT follow ``Employee.role`` when the
underlying employee gets promoted or moved. The UI
suffixes with "(当时)" / "(then)" / "(当時)" so the
operator reads it correctly; live ``Employee.role`` is
already visible in the Org tab.

Note on the JOIN: ``ContactEntry.person`` is declared
``viewonly=True`` (contacts/models.py:125-130) so we can
navigate it freely without SQLAlchemy complaining about
mutability. ``person_id`` itself is nullable (ON DELETE
SET NULL preserves history when an employee leaves), so
``ContactOut.person`` is also nullable — orphan rows
render with ``person=None``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from magi.agent.db import Employee
from magi.agent.memory.contacts.models import ContactEntry
from magi.channels.webui.api.action_items import _current_admin_id
from magi.channels.webui.api.departments import AdminGate, get_session

logger = logging.getLogger("magi.api.contacts")

router = APIRouter(tags=["contacts"])


# Cap on rows returned. A single MAGI's contacts table is
# operator-curated (the LLM only adds a row when it learns
# something new about someone); 200 is comfortable for a
# working set of known people. No pagination in v0 — see
# the plan's "Not in v0" section.
_MAX_ROWS = 200


# -- response shapes -------------------------------------------------------


class ContactPersonBrief(BaseModel):
    """JOIN'd Employee fields for the contacts table.

    Mirrors the slim ``EmployeeBrief`` shape used by
    :mod:`magi.channels.webui.api.departments` for inline
    employee references; extended with ``department_id``
    + ``department_name`` because contacts are
    person-centric and the department is the most common
    grouping the operator wants to see at a glance.

    ``display_name`` is the optional handle on the
    Employee row; the WebUI just renders ``name`` (which
    the server pre-fills with ``display_name ?? name``)
    to avoid a "do I show one or both?" branch in JS.
    """

    id: int
    name: str
    department_id: Optional[int] = None
    department_name: Optional[str] = None


class ContactOut(BaseModel):
    id: int
    # ``person_id`` is exposed separately so the UI can
    # tell apart "this row references employee #42"
    # (person_id=42, person={...}) from "this row is
    # orphaned by a deleted employee" (person_id=null,
    # person=null). Both cases render — see the orphan
    # note at module top.
    person_id: Optional[int] = None
    person: Optional[ContactPersonBrief] = None
    role: Optional[str] = None
    notes: str
    source: str
    last_seen_at: str
    created_at: str
    updated_at: str


class ContactListOut(BaseModel):
    """The GET response. ``total`` mirrors the
    ``list_for_owner`` semantics (every row owned by the
    caller, not just the page slice) so a future
    paginator has its denominator ready."""

    items: list[ContactOut]
    total: int


# -- helpers ---------------------------------------------------------------


def _iso(dt: datetime | None) -> str:
    """Render a naive-UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ``.

    All three timestamps on ``ContactEntry`` are created
    via ``datetime.utcnow`` (per the model docstring);
    no tzinfo means we strip the suffix and append ``Z``
    explicitly so the JS side never has to guess.
    """
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")


def _person_brief(emp: Employee | None) -> ContactPersonBrief | None:
    """Hydrate the JOIN'd Employee row into the inline shape.

    Returns ``None`` when the person FK is null (orphan
    row). When the Employee exists, ``name`` is resolved
    server-side as ``display_name ?? name`` — the WebUI
    never has to do the fallback.
    """
    if emp is None:
        return None
    # ``emp.department`` is the chained selectinload target;
    # ``relationship`` returns ``None`` for unassigned
    # employees, which is exactly the right default.
    dept = emp.department
    display = emp.display_name or emp.name
    return ContactPersonBrief(
        id=emp.id,
        name=display,
        department_id=emp.department_id,
        department_name=dept.name if dept is not None else None,
    )


def _serialize(row: ContactEntry) -> ContactOut:
    return ContactOut(
        id=row.id,
        person_id=row.person_id,
        person=_person_brief(row.person),
        role=row.role,
        notes=row.notes,
        source=row.source,
        last_seen_at=_iso(row.last_seen_at),
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


# -- routes ----------------------------------------------------------------


@router.get("/contacts", response_model=ContactListOut)
def list_contacts(
    request: Request,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> ContactListOut:
    """Enumerate the calling admin's contacts.

    Auth is doubled: ``AdminGate`` proves the cookie is a
    live admin session, and ``_current_admin_id`` re-reads
    the cookie to get the int ``uid`` that scopes
    the query. The defensive re-check mirrors
    :func:`magi.channels.webui.api.action_items.list_action_items`
    — the cookie is the only thing standing between the
    caller and "list everyone else's contacts", so we
    never trust the URL.

    The chained ``selectinload`` hydrates both the
    ``ContactEntry.person`` and ``Employee.department``
    joins in two extra round-trips (one per relationship,
    via SQLAlchemy's IN-list batching). Without this, the
    lazy-load would emit one extra query per row at
    render time — N+1 territory, terrible for a 200-row
    table.
    """
    admin_id = _current_admin_id(request, session)

    stmt = (
        select(ContactEntry)
        .where(ContactEntry.owner_id == admin_id)
        .options(
            selectinload(ContactEntry.person).selectinload(
                Employee.department
            )
        )
        .order_by(ContactEntry.last_seen_at.desc())
        .limit(_MAX_ROWS)
    )
    rows = list(session.scalars(stmt).all())
    return ContactListOut(
        items=[_serialize(r) for r in rows],
        total=len(rows),
    )