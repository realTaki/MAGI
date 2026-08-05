"""Hook evaluation persistence (BUS-owned short transactions).

This module wraps the :class:`HookEvaluation` ORM table behind a
small repository that the :class:`HookService` uses to:

  - Insert the ``pending`` row before the executor runs handlers.
  - Re-fetch the cached decision when a retry hits the same
    ``(subject_type, subject_id, hook_point)`` triple.
  - Update the row to ``completed`` / ``failed`` / ``timed_out``
    / ``errored`` once the executor returns.
  - List recent evaluations for the WebUI knowledge page.

The repository NEVER holds a session open across an ``await``
boundary — every public method is a short transaction per the
BUS policy (spec §12).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from magi.bus.db.base import utcnow_naive
from magi.bus.db.engine import open_session
from magi.bus.hooks.contracts import (
    HookDataScope,
    HookEvaluation,
    HookEvaluationStatus,
    HookMode,
    HookPoint,
)
from magi.bus.models.local.hook_evaluation import HookEvaluation as HookEvaluationRow


# ───────────────────────────────────────────────────────────────────── #
# Inputs / outputs
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class PendingEvaluation:
    """Inputs the repository needs to insert a new pending row."""

    hook_event_id: str
    hook_id: str
    hook_version: str
    hook_point: HookPoint
    subject_type: str
    subject_id: str
    mode: HookMode
    failure_mode: str  # HookFailureMode.value
    input_digest: str
    requested_scopes: frozenset[HookDataScope]
    attempt: int = 0
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CompletionUpdate:
    """Inputs to mark a pending row terminal."""

    hook_event_id: str
    hook_id: str
    hook_version: str
    status: HookEvaluationStatus
    decision: str | None  # HookAction.value or None for OBSERVE
    reason_code: str | None
    labels: tuple[str, ...]
    risk_score: float | None
    duration_ms: int
    error_type: str | None
    sanitized_error: str | None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RecoverySweepResult:
    """Result of :meth:`HookEvaluationRepository.recover_pending`."""

    recovered: tuple[HookEvaluation, ...]
    pending_orphans: tuple[HookEvaluation, ...]


# ───────────────────────────────────────────────────────────────────── #
# Repository
# ───────────────────────────────────────────────────────────────────── #


class HookEvaluationRepository:
    """Persistence façade for hook evaluation rows.

    Stateless — the constructor takes an optional ``state_dir`` so
    tests can target scratch directories.  Production code holds
    one instance per process, owned by the :class:`HookService`.
    """

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir

    # -- writes --------------------------------------------------------- #

    def insert_pending(self, pending: PendingEvaluation) -> HookEvaluation:
        """Insert a new pending row.

        Returns the freshly created :class:`HookEvaluation`.  If
        the unique key already exists (the BUS hit the same
        subject twice for the same handler version), the existing
        row is returned unchanged — the caller treats that as
        "decision already cached" rather than re-evaluating.
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            existing = session.scalar(
                select(HookEvaluationRow).where(
                    HookEvaluationRow.hook_event_id == pending.hook_event_id,
                    HookEvaluationRow.hook_id == pending.hook_id,
                    HookEvaluationRow.hook_version == pending.hook_version,
                )
            )
            if existing is not None:
                return _row_to_dto(existing)

            row = HookEvaluationRow(
                hook_event_id=pending.hook_event_id,
                hook_id=pending.hook_id,
                hook_version=pending.hook_version,
                hook_point=pending.hook_point.value,
                subject_type=pending.subject_type,
                subject_id=pending.subject_id,
                mode=pending.mode.value,
                failure_mode=pending.failure_mode,
                input_digest=pending.input_digest,
                requested_scopes=[s.value for s in pending.requested_scopes],
                status=HookEvaluationStatus.PENDING.value,
                started_at=now,
                completed_at=None,
                duration_ms=None,
                attempt_count=pending.attempt,
                metadata=dict(pending.metadata or {}),
                created_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # Concurrent insert won the race — re-read.
                existing = session.scalar(
                    select(HookEvaluationRow).where(
                        HookEvaluationRow.hook_event_id == pending.hook_event_id,
                        HookEvaluationRow.hook_id == pending.hook_id,
                        HookEvaluationRow.hook_version == pending.hook_version,
                    )
                )
                if existing is None:  # pragma: no cover - defensive
                    raise
                return _row_to_dto(existing)
            session.refresh(row)
            return _row_to_dto(row)

    def mark_running(self, hook_event_id: str, hook_id: str, hook_version: str) -> None:
        """Transition a pending row to ``running``."""
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(HookEvaluationRow).where(
                    HookEvaluationRow.hook_event_id == hook_event_id,
                    HookEvaluationRow.hook_id == hook_id,
                    HookEvaluationRow.hook_version == hook_version,
                )
            )
            if row is None:
                return
            row.status = HookEvaluationStatus.RUNNING.value
            row.started_at = utcnow_naive()
            session.commit()

    def mark_terminal(self, completion: CompletionUpdate) -> HookEvaluation | None:
        """Mark a row with its terminal status + decision.

        Returns ``None`` if the row was already terminal (a
        concurrent writer won the race); the caller treats that
        as "decision already cached".
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(HookEvaluationRow).where(
                    HookEvaluationRow.hook_event_id == completion.hook_event_id,
                    HookEvaluationRow.hook_id == completion.hook_id,
                    HookEvaluationRow.hook_version == completion.hook_version,
                )
            )
            if row is None:
                return None
            if row.status in {
                HookEvaluationStatus.COMPLETED.value,
                HookEvaluationStatus.FAILED.value,
                HookEvaluationStatus.TIMED_OUT.value,
                HookEvaluationStatus.ERRORED.value,
            }:
                return _row_to_dto(row)
            row.status = completion.status.value
            row.decision = completion.decision
            row.reason_code = completion.reason_code
            row.labels = list(completion.labels)
            row.risk_score = completion.risk_score
            row.duration_ms = completion.duration_ms
            row.error_type = completion.error_type
            row.sanitized_error = completion.sanitized_error
            if completion.metadata is not None:
                row.metadata = dict(completion.metadata)
            row.completed_at = now
            session.commit()
            session.refresh(row)
            return _row_to_dto(row)

    # -- reads ---------------------------------------------------------- #

    def get(
        self,
        hook_event_id: str,
        hook_id: str,
        hook_version: str,
    ) -> HookEvaluation | None:
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(HookEvaluationRow).where(
                    HookEvaluationRow.hook_event_id == hook_event_id,
                    HookEvaluationRow.hook_id == hook_id,
                    HookEvaluationRow.hook_version == hook_version,
                )
            )
            return _row_to_dto(row) if row is not None else None

    def get_event(self, hook_event_id: str) -> tuple[HookEvaluation, ...]:
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(HookEvaluationRow)
                .where(HookEvaluationRow.hook_event_id == hook_event_id)
                .order_by(HookEvaluationRow.created_at)
            ).all()
            return tuple(_row_to_dto(r) for r in rows)

    def list_by_subject(
        self,
        subject_type: str,
        subject_id: str,
        *,
        limit: int = 50,
    ) -> tuple[HookEvaluation, ...]:
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(HookEvaluationRow)
                .where(
                    HookEvaluationRow.subject_type == subject_type,
                    HookEvaluationRow.subject_id == subject_id,
                )
                .order_by(HookEvaluationRow.created_at.desc())
                .limit(limit)
            ).all()
            return tuple(_row_to_dto(r) for r in rows)

    def list_recent(
        self,
        *,
        hook_point: HookPoint | None = None,
        limit: int = 50,
    ) -> tuple[HookEvaluation, ...]:
        with open_session(self._state_dir) as session:
            stmt = select(HookEvaluationRow).order_by(
                HookEvaluationRow.created_at.desc()
            )
            if hook_point is not None:
                stmt = stmt.where(HookEvaluationRow.hook_point == hook_point.value)
            stmt = stmt.limit(limit)
            rows = session.scalars(stmt).all()
            return tuple(_row_to_dto(r) for r in rows)

    # -- recovery ------------------------------------------------------- #

    def recover_pending(self) -> RecoverySweepResult:
        """Return all rows still in ``pending`` / ``running``.

        The runtime's restart-recovery pass uses this to decide
        which evaluations need to be re-run.  Rows older than the
        configured crash window are flagged as
        ``pending_orphans`` so the operator can inspect them.

        The first version does not auto-orphan; both buckets are
        returned and the caller decides.
        """
        with open_session(self._state_dir) as session:
            pending_rows = session.scalars(
                select(HookEvaluationRow)
                .where(
                    HookEvaluationRow.status.in_({
                        HookEvaluationStatus.PENDING.value,
                        HookEvaluationStatus.RUNNING.value,
                    })
                )
                .order_by(HookEvaluationRow.created_at)
            ).all()
            dtos = tuple(_row_to_dto(r) for r in pending_rows)
            return RecoverySweepResult(recovered=dtos, pending_orphans=dtos)


# ───────────────────────────────────────────────────────────────────── #
# Row → DTO conversion
# ───────────────────────────────────────────────────────────────────── #


def _row_to_dto(row: HookEvaluationRow) -> HookEvaluation:
    return HookEvaluation(
        hook_event_id=row.hook_event_id,
        hook_id=row.hook_id,
        hook_version=row.hook_version,
        hook_point=HookPoint(row.hook_point),
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        mode=HookMode(row.mode),
        failure_mode=_failure_mode_from_value(row.failure_mode),
        input_digest=row.input_digest,
        requested_scopes=tuple(
            HookDataScope(value) for value in (row.requested_scopes or [])
        ),
        decision=row.decision,
        reason_code=row.reason_code,
        labels=tuple(row.labels or ()),
        risk_score=row.risk_score,
        status=HookEvaluationStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        duration_ms=row.duration_ms,
        error_type=row.error_type,
        sanitized_error=row.sanitized_error,
        attempt_count=row.attempt_count,
        created_at=row.created_at,
        metadata=dict(row.metadata or {}),
    )


def _failure_mode_from_value(value: str):
    from magi.bus.hooks.contracts import HookFailureMode

    return HookFailureMode(value)


__all__ = [
    "CompletionUpdate",
    "HookEvaluationRepository",
    "PendingEvaluation",
    "RecoverySweepResult",
]


# Silence "imported but unused" linters for typing-only symbols.
_ = (datetime, Iterable)
