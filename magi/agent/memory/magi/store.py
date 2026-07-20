"""MemoryStore — SQLite-backed CRUD for ``MemoryEntry``.

Same shape as :class:`magi.agent.memory.session.SessionStore`:
stateless, safe to instantiate per-request, single
``state_dir`` arg kept for caller compat. All operations
go through the shared :func:`open_session` so the
in-memory ``MemoryEntry`` returned to the caller matches
what's on disk.

Two read patterns:

  - :meth:`list_for_owner` — the system-prompt
    formatter's main call. Filters completed ``ongoing``
    rows; orders by importance desc.
  - :meth:`list_recent` — the LLM's ``list_memory``
    tool. Includes completed rows; capped at ``limit``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select

from magi.agent.db import open_session
from magi.agent.db.base import utcnow_naive
from magi.agent.memory.magi.models import (
    ALL_KINDS,
    KIND_ONGOING,
    SOURCE_EVE,
    MemoryEntry,
)


logger = logging.getLogger("magi.agent.memory.magi.store")

# Caps mirroring the Pydantic layer (the WebUI form has
# the same numbers). Truncating here too guards against
# hand-crafted tool-call bypasses.
_SUBJECT_MAX = 200
_BODY_MAX = 8 * 1024
_IMPORTANCE_MIN = 1
_IMPORTANCE_MAX = 5


@dataclass(frozen=True)
class MemoryView:
    """The in-memory shape returned to callers.

    Mirrors :class:`MemoryEntry` (a SQLAlchemy row) but
    is decoupled from the ORM class — the LLM-facing
    tool result is JSON-serialised and a dataclass
    keeps the JSON shape stable across schema changes.
    """

    id: int
    employee_id: int
    kind: str
    subject: str
    body: str
    importance: int
    source: str
    completed_at: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: MemoryEntry) -> "MemoryView":
        return cls(
            id=row.id,
            employee_id=row.employee_id,
            kind=row.kind,
            subject=row.subject,
            body=row.body,
            importance=row.importance,
            source=row.source,
            completed_at=(
                row.completed_at.isoformat().replace("+00:00", "Z")
                if row.completed_at is not None else None
            ),
            created_at=row.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=row.updated_at.isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        """JSON-friendly dict for tool results."""
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "kind": self.kind,
            "subject": self.subject,
            "body": self.body,
            "importance": self.importance,
            "source": self.source,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class MemoryStore:
    """Stateless CRUD wrapper for :class:`MemoryEntry`.

    Single ``state_dir`` arg kept for caller compat —
    the actual path is resolved once per process via
    the ORM engine singleton.
    """

    state_dir: str | os.PathLike[str]

    # -- public -----------------------------------------------------------

    def add(
        self,
        employee_id: int,
        *,
        kind: str,
        subject: str,
        body: str,
        importance: int = 3,
        source: str = SOURCE_EVE,
    ) -> MemoryView:
        """Insert one memory row.

        Validates ``kind`` against the enum-ish
        constants in :mod:`.models` and length-clamps
        ``subject`` / ``body``. Raises
        :class:`ValueError` on a bad enum (the
        LLM-facing tool catches it and returns
        ``is_error=True``).
        """
        if kind not in ALL_KINDS:
            raise ValueError(
                f"kind {kind!r} not in {sorted(ALL_KINDS)}"
            )
        if not (1 <= importance <= 5):
            importance = max(_IMPORTANCE_MIN, min(_IMPORTANCE_MAX, importance))
        subject = subject.strip()[:_SUBJECT_MAX]
        body = body.strip()[:_BODY_MAX]
        if not subject:
            raise ValueError("subject is required")
        if not body:
            raise ValueError("body is required")

        with open_session() as db:
            row = MemoryEntry(
                employee_id=employee_id,
                kind=kind,
                subject=subject,
                body=body,
                importance=importance,
                source=source,
                completed_at=None,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        logger.info(
            "memory added",
            extra={
                "memory_id": row.id,
                "employee_id": employee_id,
                "kind": kind,
                "importance": importance,
            },
        )
        return MemoryView.from_row(row)

    def get(self, memory_id: int) -> Optional[MemoryView]:
        with open_session() as db:
            row = db.get(MemoryEntry, memory_id)
        if row is None:
            return None
        return MemoryView.from_row(row)

    def list_for_owner(
        self,
        employee_id: int,
        *,
        kind: Optional[str] = None,
        include_completed: bool = False,
        limit: int = 50,
    ) -> list[MemoryView]:
        """Read entries owned by ``employee_id``.

        Defaults:

          - ``kind=None`` means "any kind" (the
            system-prompt formatter passes no kind
            so it sees both important and ongoing).
          - Completed ``ongoing`` rows are filtered
            out unless ``include_completed=True`` (so
            the system prompt doesn't accumulate
            "done" rows forever — the operator can
            prune via the dashboard or the
            ``delete_memory`` tool).

        Ordered by ``importance`` desc, then by
        ``updated_at`` desc so the freshest high-
        importance items rise to the top of the
        system-prompt block.
        """
        with open_session() as db:
            stmt = select(MemoryEntry).where(
                MemoryEntry.employee_id == employee_id
            )
            if kind is not None:
                stmt = stmt.where(MemoryEntry.kind == kind)
            if not include_completed:
                stmt = stmt.where(
                    # Either the row is not "ongoing" OR
                    # it's "ongoing" but not yet
                    # completed. SQL translates to:
                    #   kind != 'ongoing'
                    #   OR completed_at IS NULL
                    (MemoryEntry.kind != KIND_ONGOING)
                    | (MemoryEntry.completed_at.is_(None))
                )
            stmt = (
                stmt
                .order_by(
                    MemoryEntry.importance.desc(),
                    MemoryEntry.updated_at.desc(),
                )
                .limit(limit)
            )
            rows = db.execute(stmt).scalars().all()
        return [MemoryView.from_row(r) for r in rows]

    def update(
        self,
        memory_id: int,
        *,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        importance: Optional[int] = None,
    ) -> MemoryView:
        """Patch one or more mutable fields.

        ``kind`` and ``employee_id`` are immutable
        (changing them would silently mis-categorise
        the row); the LLM-facing tool documents this
        and returns ``is_error=True`` if asked.
        ``completed_at`` is set via :meth:`complete`
        rather than this method so the call site
        reads cleanly.
        """
        with open_session() as db:
            row = db.get(MemoryEntry, memory_id)
            if row is None:
                raise LookupError(f"memory {memory_id!r} not found")
            if subject is not None:
                row.subject = subject.strip()[:_SUBJECT_MAX]
            if body is not None:
                row.body = body.strip()[:_BODY_MAX]
            if importance is not None:
                row.importance = max(
                    _IMPORTANCE_MIN, min(_IMPORTANCE_MAX, importance)
                )
            db.commit()
            db.refresh(row)
        logger.info(
            "memory updated",
            extra={"memory_id": memory_id},
        )
        return MemoryView.from_row(row)

    def complete(self, memory_id: int) -> MemoryView:
        """Mark an ``ongoing`` row done.

        Implementation: set ``completed_at`` to the
        current UTC. The system-prompt formatter
        filters these out, so the row stays in the
        table for the audit trail but drops out of
        the LLM's working memory.
        """
        with open_session() as db:
            row = db.get(MemoryEntry, memory_id)
            if row is None:
                raise LookupError(f"memory {memory_id!r} not found")
            row.completed_at = utcnow_naive()
            db.commit()
            db.refresh(row)
        logger.info(
            "memory completed",
            extra={"memory_id": memory_id},
        )
        return MemoryView.from_row(row)

    def delete(self, memory_id: int) -> bool:
        """Remove one row. ``True`` if it existed.

        Idempotent: deleting a non-existent id is a
        no-op. The LLM-facing tool surfaces this
        as a successful ``is_error=False`` so a
        retry doesn't look like a failure.
        """
        with open_session() as db:
            row = db.get(MemoryEntry, memory_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
        logger.info(
            "memory deleted",
            extra={"memory_id": memory_id},
        )
        return True


__all__ = [
    "MemoryStore",
    "MemoryView",
]