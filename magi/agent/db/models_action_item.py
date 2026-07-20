"""ORM table ``action_items`` — the dashboard's to-do surface.

A small "to-do" surface — the dashboard shows these in
the "Action Items" sidebar entry. The first concrete
use case is ``kind='llm_credentials_missing'``: every
new admin gets one so the dashboard nudges them to
set their provider + API key before they chat. C4
will reuse the same table for EVE-driven follow-ups
(kind strings like ``eve_followup_meeting``).

Schema is intentionally kind-agnostic — every row
carries a stable ``kind`` + human-readable ``title`` /
``description`` / ``target_url``. No ``payload_json``
blob (YAGNI for the rows we can foresee; add it later
if C4 needs structured per-kind fields).

FK policy is ``ON DELETE SET NULL`` rather than
CASCADE: ``save_admin`` deletes old admin rows; cascade
would wipe their action history silently. SET NULL
leaves an ``employee_id IS NULL`` orphan that the
post-commit sweep in ``save_admin`` re-binds to the
freshly-recreated admin, so "remove admin and re-add"
naturally surfaces the prompt again instead of
erasing it.

FKs reference :class:`Employee` in
:mod:`magi.agent.db.models_employee`. Type-only import
under TYPE_CHECKING — FK strings resolve at mapper
config time.
"""

from __future__ import annotations

from datetime import datetime

from magi.agent.db.base import utcnow_naive
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


if TYPE_CHECKING:
    from magi.agent.db.models_employee import Employee


class ActionItem(Base):
    """A to-do surfaced to an admin in the dashboard.

    Created by system paths (``save_admin`` etc.) and,
    from C4, by EVEs that want to nudge the operator
    about a follow-up. Dismissed / completed by the
    operator via
    ``POST /api/action_items/{id}/complete`` — auto-
    completion is deliberately out of scope (the operator
    may want to dismiss a row for reasons unrelated to
    the underlying state).
    """

    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    # See class docstring on the SET NULL choice. Nullable
    # because the FK target can disappear (admin removed),
    # leaving the action item as an orphan until something
    # re-binds it.
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stable identifier per row category. Free-form string;
    # the schema doesn't enforce a closed enum so C4 can
    # add new kinds without a migration. Picked to be
    # short + readable in /api/action_items?kind=<here>
    # URLs.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    # Where the "去设置" button navigates. Currently a
    # path relative to the dashboard; future in-app tab
    # switches can replace it with a deep-link state.
    target_url: Mapped[str | None] = mapped_column(String(500))
    # "normal" (default) or "high" — C4 uses "high" for
    # time-sensitive follow-ups. Not a closed enum here
    # for the same reason as ``kind``.
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal"
    )
    # Who created the row. "system" for save_admin /
    # similar, "eve" when C4 EVE-driven rows land, "user"
    # for future operator-authored reminders. Useful for
    # grouping + filtering later.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="system"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    # Null = open. The "I clicked 完成" stamp.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    completed_by_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional reason captured at complete-time; useful
    # for C4 EVE-driven rows where the operator may say
    # "I'll do it next week" (the EVE then reads it back).
    completion_note: Mapped[str | None] = mapped_column(String(500))
    # Hidden without recording "I did it". Distinct from
    # completed_at IS NOT NULL — both remove from the
    # open list, but a dismissed row never claims the
    # underlying action was performed. Used by the future
    # "hide this prompt" affordance; v0 leaves dismissed
    # at False.
    dismissed: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Viewonly relationships so route code never mutates
    # Employee via the ActionItem collection.
    employee: Mapped["Employee | None"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )
    completed_by: Mapped["Employee | None"] = relationship(
        foreign_keys=[completed_by_employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"ActionItem(id={self.id}, kind={self.kind!r}, "
            f"employee_id={self.employee_id}, "
            f"completed={self.completed_at is not None}, "
            f"dismissed={self.dismissed})"
        )