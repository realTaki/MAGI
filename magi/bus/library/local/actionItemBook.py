"""ActionItemBook — dashboard to-do inbox.

Pure CRUD over the ``action_items`` table. The Book owns
**data access** only; the **decision** of what to write,
when, and with which provenance tag belongs to callers
(LLM-driven tools under ``magi.tools`` and proactive
policies under ``magi.proactive``).

Schema for the ``action_items`` table.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import Strict, StringConstraints
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import enum_column, utcnow_naive
from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin, record

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

# Default visibility window for completed rows when the caller
# asks for ``include_completed=True``. Mirrors the dashboard's
# default mix.
_COMPLETED_VISIBLE_DAYS = 7

# Column-length invariants. Mirror the ORM column
# declarations (``String(200)`` / ``String(1000)`` /
# ``String(500)``) and the ``completion_note`` /
# ``description`` business caps. The Book enforces them so
# every caller — chat-driven tool, dashboard API, proactive
# policy, future agent loop — gets the same validation
# without each path re-implementing length checks.
_TITLE_MAX = 200
_DESCRIPTION_MAX = 1000
_TARGET_URL_MAX = 500
_COMPLETION_NOTE_MAX = 500


@record
class ActionItem(BaseRecord):
    """A to-do surfaced to an operator in the dashboard.

    Frozen DTO returned to callers; the Book maps every ORM
    row to one of these via :meth:`BaseBook._row_to_dto`.
    ``to_dict`` returns the public-facing wire shape — ISO
    timestamps, ``None`` for unset optionals — matching the
    bus's ``ActionItemView`` contract that the API layer
    and LLM tool both consume.
    """

    contact_id: Annotated[int, Strict()]  # 所属联系人 ID
    title: Annotated[
        str, Strict(), StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]  # 待办标题
    description: Annotated[str, Strict(), StringConstraints(max_length=1000)] | None = None
    target_url: Annotated[str, Strict(), StringConstraints(max_length=500)] | None = None
    priority: ActionPriority = ActionPriority.NORMAL  # 优先级（normal/high）
    due_date: Annotated[datetime, Strict()] | None = None  # 截止日期
    source: ActionSource = ActionSource.PROACTIVE  # 来源（user/proactive）
    completed_at: Annotated[datetime, Strict()] | None = None  # 完成时间（None=未完成）
    completion_note: Annotated[str, Strict(), StringConstraints(max_length=500)] | None = None
    dismissed: Annotated[bool, Strict()] = False  # 是否已被 dismiss（隐藏但未完成）

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
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Optional longer text — surfaces under the title in the
    # dashboard. ``Text`` rather than ``String(N)`` to match
    # the column shape.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # In-app deep-link target for the row's "go to" button.
    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
    completion_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
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

    Timestamp mapping is inherited: ``BaseBook._row_to_dto`` keeps every
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
        source: str | None = None,
        completed_visible_days: int = _COMPLETED_VISIBLE_DAYS,
    ) -> list[ActionItem]:
        """List an operator's action items.

        ``include_completed=False`` returns only open,
        non-dismissed rows. ``include_completed=True`` also
        surfaces rows completed/dismissed within the last
        ``completed_visible_days`` days (matches the
        dashboard's default mix).

        ``source`` narrows to one provenance tag — pass
        :data:`ActionSource.USER` for the LLM tool menu (excludes
        proactive nudges that live on the dashboard's own
        pane), :data:`ActionSource.PROACTIVE` for proactive-only
        views, or ``None`` for everything the operator
        owns.
        """
        with self._session() as s:
            stmt = select(_ActionItemRow).where(
                _ActionItemRow.contact_id == owner_contact_id,
            )
            if source is not None:
                if source not in ActionSource:
                    raise ValueError(
                        f"source must be one of "
                        f"{sorted(s.value for s in ActionSource)!r} "
                        f"or None, got {source!r}"
                    )
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
            return [self._row_to_dto(r) for r in rows]

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
        Raises :class:`ValueError` if ``note`` exceeds
        :data:`COMPLETION_NOTE_MAX` characters.
        """
        if note is not None and len(note) > _COMPLETION_NOTE_MAX:
            raise ValueError(
                f"completion_note length {len(note)} exceeds maximum {_COMPLETION_NOTE_MAX}"
            )
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
            return self._row_to_dto(row)


__all__ = [
    "ActionItem",
    "ActionItemBook",
    "ActionPriority",
    "ActionSource",
    "_ActionItemRow",
]
