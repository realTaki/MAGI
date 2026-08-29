"""ActionItemBook — dashboard to-do inbox.

Pure CRUD over the ``action_items`` table. The Book owns
**data access** only; the **decision** of what to write,
when, and with which provenance tag belongs to callers
(LLM-driven tools under ``magi.tools`` and proactive
policies under ``magi.proactive``).

Schema for the ``action_items`` table.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from magi.old_bus.bases.db.base import enum_column, utcnow_naive

# -- public dataclass ----------------------------------------------------


# Provenance tags — propagated onto ``ActionItem.source``.
# Two-way split by **causation** (not mechanism):
#
#   * ``ActionSource.USER``      — the operator caused this row.
#     Covers: dashboard channel API writes, chat-driven
#     tool calls (the operator's chat turn kicked the
#     LLM, even when the LLM picked the tool autonomously),
#     and any future user-facing surface.
#   * ``ActionSource.PROACTIVE`` — the system discovered /
#     scheduled this row without an operator in the loop.
#     Covers: proactive policies (e.g. the onboarding
#     credentials nudge in ``magi.proactive.worker``),
#     cron-triggered agents, system-defined nudges.
#
# Dashboards and future filters group rows by this tag.
class ActionSource(StrEnum):
    USER = "user"
    PROACTIVE = "proactive"


# Priority enum — the LLM tool's UI mentions "normal" and
# "high" only; other values are reserved for system paths.
#
# ``StrEnum`` rather than bare constants so typos are caught
# at import/lookup time instead of silently comparing False:
# every member is still a ``str`` (``ActionPriority.HIGH ==
# "high"``), so ORM columns, JSON serialisation and existing
# rows keep working unchanged. Membership checks use
# ``x in ActionSource`` (Python 3.12+ ``StrEnum`` supports
# ``in`` against string values directly), so no separate
# ``ALL_*`` frozenset is needed.
class ActionPriority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"

@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ActionItem(BaseRecord):
    """A to-do surfaced to an operator in the dashboard.

    Frozen DTO returned to callers; the Book maps every ORM
    row to one of these via :meth:`BaseRecord.from_row`.
    ``to_dict`` returns the public-facing wire shape — ISO
    timestamps, ``None`` for unset optionals — matching the
    bus's ``ActionItemView`` contract that the API layer
    and LLM tool both consume.
    """

    contact_id: int  # 所属联系人 ID
    title: str  # 待办标题
    description: str | None = None
    target_url: str | None = None
    priority: ActionPriority = ActionPriority.NORMAL  # 优先级（normal/high）
    due_date: datetime | None = None  # 截止日期
    source: ActionSource = ActionSource.PROACTIVE  # 来源（user/proactive）
    completed_at: datetime | None = None  # 完成时间（None=未完成）
    completion_note: str | None = None
    dismissed: bool = False  # 是否已被 dismiss（隐藏但未完成）

# -- internal ORM --------------------------------------------------------


class _ActionItemRow(BaseRecordMixin):
    __tablename__ = "action_items"

    # ``SET NULL`` mirrors the previous policy: removing an
    # operator leaves the row as an orphan rather than wiping
    # action history. Re-binding is handled by the caller.
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional longer text — surfaces under the title in the
    # dashboard. Free text, no length cap.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # In-app deep-link target for the row's "go to" button.
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[ActionPriority] = mapped_column(
        enum_column(ActionPriority),
        nullable=False,
        default=ActionPriority.NORMAL,
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Provenance tag — "user" / "proactive". ``ActionSource.PROACTIVE``
    # is the column default so any future writer that forgets
    # to pass ``source=`` defaults to the safe side (system
    # actions are non-repudiable; user actions are auditable).
    source: Mapped[ActionSource] = mapped_column(
        enum_column(ActionSource),
        nullable=False,
        default=ActionSource.PROACTIVE,
    )
    # Null = still open. The "I clicked 完成" stamp.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optional reason captured at complete-time.
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Distinct from completion: a dismissed row never claims
    # the underlying action was performed, but is hidden from
    # the open list just the same.
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# -- Book ----------------------------------------------------------------


class ActionItemBook(BaseBook[_ActionItemRow, ActionItem]):
    """CRUD for the ``action_items`` dashboard surface.

    Pure data access. Callers — chat-driven tools in
    :mod:`magi.tools.tasks` and proactive policies in
    :mod:`magi.proactive` — pass the ``source`` tag
    explicitly so the audit trail reflects who caused
    the write.

    Timestamp mapping is inherited: ``BaseRecord.from_row`` keeps every
    ``datetime`` column intact. This Book has no special serialisation path.
    """

    model_cls = _ActionItemRow
    record_cls = ActionItem

    # -- single-row reads -------------------------------------------------

    # Note: a ``has_open(contact_id, kind)`` exists-check that lived
    # here previously has been removed. Idempotency-on-first-
    # write is a policy concern (proactive decides which
    # specific rows to de-dupe on, usually by ``title``), not
    # a Book primitive — the only caller, the credentials
    # nudge in :mod:`magi.proactive.worker`,
    # composes the check via :meth:`list_actions` with
    # ``source=ActionSource.PROACTIVE`` and a client-side title
    # match. The Book stays query-neutral.

    # -- list reads -------------------------------------------------------

    def list_actions(
        self,
        *,
        owner_contact_id: int,
        include_completed: bool,
        source: ActionSource | None = None,
        completed_visible_days: int = 1,
    ) -> list[ActionItem]:
        """List an operator's action items.

        ``include_completed=False`` returns only open,
        non-dismissed rows. ``include_completed=True`` also
        surfaces rows completed/dismissed within the last
        ``completed_visible_days`` days. Visibility is caller
        policy, but the Book still ships a 1-day defensive
        default so a caller that forgets the argument gets a
        tight recent-history window instead of either an empty
        list (zero days) or all of history (no cap).

        ``source`` narrows to one provenance tag — pass
        :data:`ActionSource.USER` for the LLM tool menu (excludes
        proactive nudges that live on the dashboard's own
        pane), :data:`ActionSource.PROACTIVE` for proactive-only
        views, or ``None`` for everything the operator
        owns.
        """
        if completed_visible_days < 0:
            raise ValueError("completed_visible_days must be non-negative")
        with self._session() as s:
            stmt = select(_ActionItemRow).where(
                _ActionItemRow.contact_id == owner_contact_id,
            )
            if source is not None:
                stmt = stmt.where(_ActionItemRow.source == source)
            if not include_completed:
                stmt = stmt.where(
                    _ActionItemRow.completed_at.is_(None),
                    _ActionItemRow.dismissed.is_(False),
                )
            else:
                cutoff = utcnow_naive() - timedelta(days=completed_visible_days)
                stmt = stmt.where(
                    (_ActionItemRow.completed_at.is_(None))
                    | (_ActionItemRow.completed_at >= cutoff)
                )
            stmt = stmt.order_by(
                _ActionItemRow.completed_at.is_(None).desc(),
                _ActionItemRow.priority.desc(),
                _ActionItemRow.created_at.desc(),
            )
            rows = s.scalars(stmt).all()
            return [self.record_cls.from_row(r) for r in rows]

    # -- writes -----------------------------------------------------------

    def complete(
        self,
        *,
        action_item_id: int,
        note: str | None = None,
    ) -> ActionItem | None:
        """Mark an action item complete.

        Pure data write — the Book does **not** verify
        ownership. The caller (chat-driven tool, proactive
        policy, dashboard route) is the layer that knows
        who's authorised to close which row; it must
        already have done so via a prior :meth:`get` and a
        ``row.contact_id == caller_contact_id`` check before reaching
        here.

        Owns the ``completion_note`` length invariant
        (≤500 chars, mirrors the ORM column). Idempotent:
        re-calling on an already-completed row is a no-op;
        the existing row is returned untouched so the LLM
        tool can serialise the same DTO either way.
        ``note`` is captured only when there is actually a
        transition (open → closed) — second passes do not
        overwrite the original note.

        Returns ``None`` when the row doesn't exist.
        """
        with self._session() as s:
            row = s.get(_ActionItemRow, action_item_id)
            if row is None:
                return None
            if row.completed_at is None:
                row.completed_at = utcnow_naive()
                if note is not None:
                    row.completion_note = note
                s.commit()
                s.refresh(row)
            return self.record_cls.from_row(row)


__all__ = [
    "ActionItem",
    "ActionItemBook",
    "ActionPriority",
    "ActionSource",
    "_ActionItemRow",
]
