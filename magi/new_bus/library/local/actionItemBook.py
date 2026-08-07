"""ActionItemBook — dashboard to-do inbox.

Pure CRUD over the ``action_items`` table. The Book owns
**data access** only; the **decision** of what to write,
when, and with which provenance tag belongs to callers
(LLM-driven tools under ``magi.tools`` and proactive
policies under ``magi.proactive``).

Schema mirrors the old bus's ``action_items`` table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


# Provenance tags — propagated onto ``ActionItem.source``.
# Two-way split by **causation** (not mechanism):
#
#   * ``SOURCE_USER``      — the operator caused this row.
#     Covers: dashboard channel API writes, chat-driven
#     tool calls (the operator's chat turn kicked the
#     LLM, even when the LLM picked the tool autonomously),
#     and any future user-facing surface.
#   * ``SOURCE_PROACTIVE`` — the system discovered /
#     scheduled this row without an operator in the loop.
#     Covers: proactive policies (e.g. the onboarding
#     credentials nudge in ``magi.proactive.credentials_nudge``),
#     cron-triggered agents, system-defined nudges.
#
# Dashboards and future filters group rows by this tag.
SOURCE_USER = "user"
SOURCE_PROACTIVE = "proactive"
ALL_SOURCES = frozenset({SOURCE_USER, SOURCE_PROACTIVE})

# Priority enum — the LLM tool's UI mentions "normal" and
# "high" only; other values are reserved for system paths.
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
ALL_PRIORITIES = frozenset({PRIORITY_NORMAL, PRIORITY_HIGH})

# Default visibility window for completed rows when the caller
# asks for ``include_completed=True``. Mirrors the dashboard's
# default mix.
_COMPLETED_VISIBLE_DAYS = 7


@dataclass(frozen=True, slots=True)
class ActionItem:
    """A to-do surfaced to an operator in the dashboard.

    Frozen DTO returned to callers; the Book maps every ORM
    row to one of these via :meth:`BaseBook._row_to_dto`.
    ``to_dict`` returns the public-facing wire shape — ISO
    timestamps, ``None`` for unset optionals — matching the
    old bus's ``ActionItemView`` contract that the API layer
    and LLM tool both consume.
    """

    id: int
    uid: int
    title: str
    description: str | None = None
    target_url: str | None = None
    priority: str = PRIORITY_NORMAL
    due_date: datetime | None = None
    source: str = SOURCE_PROACTIVE
    created_at: datetime | None = None
    completed_at: datetime | None = None
    completed_by_uid: int | None = None
    completion_note: str | None = None
    dismissed: bool = False

    def to_dict(self) -> dict:
        """Wire-shape for JSON serialisation.

        Mirrors ``magi.bus.jobs.protocols.action_item.ActionItemView``
        so the WebUI API and the LLM tool see the same field
        names they saw pre-migration.

        No timestamp coercion here: ``BaseBook._row_to_dto`` has
        already rendered every ``datetime`` column through
        :func:`~magi.new_bus.library.base.to_iso`, so the three
        timestamp fields are ISO-8601 ``Z`` strings by the time
        this runs.
        """
        return asdict(self)


# -- internal ORM --------------------------------------------------------


class _ActionItemRow(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # ``SET NULL`` mirrors the old bus policy: removing an
    # operator leaves the row as an orphan rather than wiping
    # action history. Re-binding is handled by the caller.
    uid: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Optional longer text — surfaces under the title in the
    # dashboard. ``Text`` rather than ``String(N)`` to match
    # the old bus column shape.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # In-app deep-link target for the row's "go to" button.
    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PRIORITY_NORMAL,
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Provenance tag — "user" / "proactive". ``SOURCE_PROACTIVE``
    # is the column default so any future writer that forgets
    # to pass ``source=`` defaults to the safe side (system
    # actions are non-repudiable; user actions are auditable).
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_PROACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )
    # Null = still open. The "I clicked 完成" stamp.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by_uid: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True,
    )
    # Optional reason captured at complete-time.
    completion_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Distinct from completion: a dismissed row never claims
    # the underlying action was performed, but is hidden from
    # the open list just the same.
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# -- helpers -------------------------------------------------------------


# -- Book ----------------------------------------------------------------


class ActionItemBook(BaseBook[_ActionItemRow, ActionItem]):
    """CRUD for the ``action_items`` dashboard surface.

    Pure data access. Callers — chat-driven tools in
    :mod:`magi.tools.tasks` and proactive policies in
    :mod:`magi.proactive` — pass the ``source`` tag
    explicitly so the audit trail reflects who caused
    the write.

    Timestamp serialisation is inherited: ``BaseBook._row_to_dto``
    renders every ``datetime`` column through
    :func:`~magi.new_bus.library.base.to_iso` (ISO-8601 with a
    trailing ``Z``). This Book used to override the mapper solely
    to get that ``Z``; the base now does it for all 18 Books.
    """

    model_cls = _ActionItemRow
    dto_cls = ActionItem

    # -- single-row reads -------------------------------------------------

    def get(self, *, item_id: int) -> ActionItem | None:
        with self._session() as s:
            row = s.scalar(
                select(_ActionItemRow).where(_ActionItemRow.id == item_id)
            )
            return self._row_to_dto(row) if row else None

    # Note: a ``has_open(uid, kind)`` exists-check that lived
    # here previously has been removed. Idempotency-on-first-
    # write is a policy concern (proactive decides which
    # specific rows to de-dupe on, usually by ``title``), not
    # a Book primitive — the only caller, the credentials
    # nudge in :mod:`magi.proactive.credentials_nudge`,
    # composes the check via :meth:`list_actions` with
    # ``source=SOURCE_PROACTIVE`` and a client-side title
    # match. The Book stays query-neutral.

    # -- list reads -------------------------------------------------------

    def list_actions(
        self,
        *,
        owner_uid: int,
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
        :data:`SOURCE_USER` for the LLM tool menu (excludes
        proactive nudges that live on the dashboard's own
        pane), :data:`SOURCE_PROACTIVE` for proactive-only
        views, or ``None`` for everything the operator
        owns.
        """
        with self._session() as s:
            stmt = select(_ActionItemRow).where(
                _ActionItemRow.uid == owner_uid,
            )
            if source is not None:
                if source not in ALL_SOURCES:
                    raise ValueError(
                        f"source must be one of "
                        f"{sorted(ALL_SOURCES)!r} or None, got {source!r}"
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

    def add(
        self,
        *,
        uid: int,
        title: str,
        description: str | None = None,
        target_url: str | None = None,
        priority: str = PRIORITY_NORMAL,
        due_date: datetime | None = None,
        source: str = SOURCE_PROACTIVE,
    ) -> ActionItem:
        """Insert one action item row.

        Callers pass ``source=`` explicitly — chat-driven
        tools pass :data:`SOURCE_USER`, proactive policies
        pass :data:`SOURCE_PROACTIVE`. The default
        (:data:`SOURCE_PROACTIVE`) is the safe side: a
        writer that forgets to tag is treated as system
        action, which is non-repudiable.
        """
        if priority not in ALL_PRIORITIES:
            raise ValueError(
                f"priority must be one of {sorted(ALL_PRIORITIES)!r}, got {priority!r}"
            )
        if source not in ALL_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(ALL_SOURCES)!r}, got {source!r}"
            )
        with self._session() as s:
            row = _ActionItemRow(
                uid=uid,
                title=title,
                description=description,
                target_url=target_url,
                priority=priority,
                due_date=due_date,
                source=source,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def complete(
        self,
        *,
        action_item_id: int,
        note: str | None = None,
        completed_by_uid: int | None = None,
    ) -> ActionItem | None:
        """Mark an action item complete.

        Pure data write — the Book does **not** verify
        ownership. The caller (chat-driven tool, proactive
        policy, dashboard route) is the layer that knows
        who's authorised to close which row; it must
        already have done so via a prior :meth:`get` and a
        ``row.uid == caller_uid`` check before reaching
        here.

        Idempotent: re-calling on an already-completed row
        is a no-op; the existing row is returned untouched
        so the LLM tool can serialize the same DTO either
        way. ``note`` is captured only when there is
        actually a transition (open → closed) — second
        passes do not overwrite the original note.

        Returns ``None`` when the row doesn't exist.
        """
        with self._session() as s:
            row = s.get(_ActionItemRow, action_item_id)
            if row is None:
                return None
            if row.completed_at is None:
                row.completed_at = utcnow_naive()
                if completed_by_uid is not None:
                    row.completed_by_uid = completed_by_uid
                if note is not None:
                    row.completion_note = note
                s.commit()
                s.refresh(row)
            return self._row_to_dto(row)


__all__ = [
    "ActionItem",
    "ActionItemBook",
    "PRIORITY_NORMAL",
    "PRIORITY_HIGH",
    "ALL_PRIORITIES",
    "SOURCE_USER",
    "SOURCE_PROACTIVE",
    "ALL_SOURCES",
    "_ActionItemRow",
]