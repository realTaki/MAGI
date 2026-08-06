"""BaseJobQueue — durable job queue with lease / reclaim / inline-publish.

Each Queue wraps one (or a small group of) ORM tables and provides
``publish / claim / submit_result / get_result`` plus a public
``inline=True`` publish path for single-direction writes that don't
need a worker round-trip.

The inline-publish pattern
--------------------------

``BaseJobQueue.publish(job, inline=True)`` is for jobs whose "work"
is just a side-effect the publisher can do itself (e.g. waking a
provider worker, in-process channel dispatch).  Setting
``inline=True`` causes the row to be inserted, immediately
claimed (with ``leased_by="__inline__"``), the subclass's
``_run_inline(...)`` to execute, and the result to be submitted —
all before ``publish`` returns.  No external worker will ever
claim the row.

Subclasses that want to support inline-publish override
:meth:`_run_inline`.  The base class raises ``NotImplementedError``
if ``inline=True`` and the subclass hasn't implemented the hook.

Subclass contract
-----------------

Three class attributes:

- ``job_model: type[RowT]`` — the ORM class for the queue table
- ``job_cls: type[JobT]`` — the public ``Job`` dataclass (publisher
  input)
- ``result_cls: type[ResultT]`` — the public ``Result`` dataclass
  (worker output, must have ``job_id: str`` and ``success: bool``)

Two required methods:

- ``publish(job, *, inline=False, **kwargs) -> str`` — insert a
  pending row, optionally run inline, return ``job_id``
- ``_insert_pending(session, job, **kwargs) -> RowT`` — concrete
  insert (called by the base class)

One optional method (for inline-publish):

- ``_run_inline(session, *, job_id, **kwargs) -> ResultT`` — run
  the work synchronously, return the result to be submitted
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import EngineFactory

DEFAULT_LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
INLINE_PUBLISHER = "__inline__"

RowT = TypeVar("RowT", bound=Base)
JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


class BaseJobQueue(Generic[RowT, JobT, ResultT]):
    """Base for new_bus job queues.

    Subclasses set ``job_model`` / ``job_cls`` / ``result_cls`` /
    ``natural_key_attr`` and implement ``_insert_pending``.
    Optionally ``_run_inline`` if they support ``inline=True`` publish.
    """

    job_model: type[RowT]
    job_cls: type[JobT]
    result_cls: type[ResultT]
    #: Column name on ``job_model`` that holds the natural key
    #: (e.g. ``"job_id"``, ``"attempt_id"``, ``"delivery_id"``,
    #: ``"invocation_id"``, ``"event_id"``).  Defaults to ``"job_id"``.
    natural_key_attr: str = "job_id"

    def __init__(
        self,
        factory: EngineFactory,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ):
        self._factory = factory
        self._lease_seconds = lease_seconds

    # -- public: lifecycle ------------------------------------------------

    def publish(self, job: JobT, *, inline: bool = False, **kwargs) -> str:
        """Insert a job row; return its natural key (``job_id`` etc.).

        If ``inline=True``, the row is immediately claimed, the
        subclass's ``_run_inline`` runs in-process, and the result
        is submitted — all before this call returns.  The job's
        terminal state is "completed" or "failed" by the time
        the caller sees the returned ``job_id``.
        """
        with self._factory.session() as s:
            row = self._insert_pending(s, job, **kwargs)
            s.flush()
            job_id = str(getattr(row, self.natural_key_attr))
            if inline:
                now = utcnow_naive()
                row.status = "processing"
                if hasattr(row, "leased_by"):
                    row.leased_by = INLINE_PUBLISHER
                if hasattr(row, "leased_until"):
                    row.leased_until = None
                if hasattr(row, "attempts"):
                    row.attempts = 1
                if hasattr(row, "started_at"):
                    row.started_at = now
                s.flush()
                result = self._run_inline(s, job_id=job_id, **kwargs)
                self._submit(s, job_id=job_id, result=result)
            s.commit()
            return job_id

    def claim(self, *, worker_id: str) -> JobT | None:
        """Claim the next pending or expired-lease job for ``worker_id``.

        Returns the Job dataclass on success, or ``None`` if the
        queue is empty / all jobs are leased-but-not-yet-expired.
        """
        with self._factory.session() as s:
            row = self._claim(s, worker_id=worker_id)
            s.commit()
            return self._row_to_job(row) if row else None

    def submit_result(self, *, job_id: str, result: ResultT) -> None:
        """Worker reports completion: status=completed/failed + result fields."""
        with self._factory.session() as s:
            self._submit(s, job_id=job_id, result=result)
            s.commit()

    def get_result(self, *, job_id: str) -> ResultT | None:
        """Read the final result for ``job_id`` (None if still in flight)."""
        with self._factory.session() as s:
            return self._get_result(s, job_id=job_id)

    def recover_expired_leases(self) -> int:
        """Reclaim all 'processing' rows whose lease expired.

        Returns the count reclaimed.  Subclasses may override if
        the standard "set status='pending', leased_by=NULL,
        leased_until=NULL" pattern doesn't fit.
        """
        now = utcnow_naive()
        count = 0
        with self._factory.session() as s:
            rows = s.scalars(
                select(self.job_model)
                .where(
                    self.job_model.status == "processing",
                    self.job_model.leased_until < now,
                )
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                if hasattr(row, "status"):
                    row.status = "pending"
                if hasattr(row, "leased_by"):
                    row.leased_by = None
                if hasattr(row, "leased_until"):
                    row.leased_until = None
                count += 1
            s.commit()
        return count

    # -- subclass hooks --------------------------------------------------

    def _insert_pending(self, session: Session, job: JobT, **kwargs) -> RowT:
        """Subclass: create the ORM row, status='pending'.  Must flush."""
        raise NotImplementedError

    def _run_inline(self, session: Session, *, job_id: str, **kwargs) -> ResultT:
        """Subclass OPTIONAL: synchronous work for ``inline=True`` publish.

        Default: raises ``NotImplementedError``.  Subclasses that
        can consume their own jobs synchronously override this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support inline=True"
        )

    # -- private: standard claim/submit machinery -----------------------

    def _claim(self, session: Session, *, worker_id: str) -> RowT | None:
        now = utcnow_naive()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        while True:
            candidate = self._pick_candidate(session, now)
            if candidate is None:
                return None
            if (
                getattr(candidate, "status", None) == "processing"
                and getattr(candidate, "attempts", 0) >= MAX_ATTEMPTS
            ):
                exhausted = self._make_exhausted_result(candidate)
                self._submit(session, job_id=self._key_of(candidate), result=exhausted)
                session.flush()
                continue
            is_reclaim = getattr(candidate, "status", None) == "processing"
            if hasattr(candidate, "status"):
                candidate.status = "processing"
            if hasattr(candidate, "leased_by"):
                candidate.leased_by = worker_id
            if hasattr(candidate, "leased_until"):
                candidate.leased_until = lease_until
            if hasattr(candidate, "attempts"):
                candidate.attempts += 1
            if not is_reclaim and hasattr(candidate, "started_at"):
                candidate.started_at = now
            return candidate

    def _pick_candidate(self, session: Session, now: datetime) -> RowT | None:
        if not hasattr(self.job_model, "status"):
            return None
        return session.scalar(
            select(self.job_model)
            .where(or_(
                self.job_model.status == "pending",
                and_(
                    self.job_model.status == "processing",
                    self.job_model.leased_until < now,
                ),
            ))
            .order_by(
                getattr(self.job_model, "created_at", self.job_model.id),
                self.job_model.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def _submit(self, session: Session, *, job_id: str, result: ResultT) -> None:
        row = session.get(self.job_model, job_id)
        if row is None:
            return
        now = utcnow_naive()
        if hasattr(row, "status"):
            row.status = "completed" if result.success else "failed"
        if hasattr(row, "completed_at"):
            row.completed_at = now
        self._write_result_to_job(row, result)

    def _get_result(self, session: Session, *, job_id: str) -> ResultT | None:
        row = session.get(self.job_model, job_id)
        if row is None:
            return None
        if getattr(row, "status", None) not in ("completed", "failed"):
            return None
        return self._read_result_from_job(row)

    # -- private: reflection-based ORM <-> dataclass mapping -------------

    def _key_of(self, row: RowT) -> str:
        """Return the natural key of a row.

        Prefers the subclass's ``natural_key_attr``; falls back to
        ``id`` if the natural key column is not populated.
        """
        for attr in (self.natural_key_attr, "id"):
            if hasattr(row, attr):
                val = getattr(row, attr)
                if val is not None:
                    return str(val)
        return ""

    def _row_to_job(self, row: RowT) -> JobT:
        kwargs: dict = {}
        for f in dataclasses.fields(self.job_cls):
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return self.job_cls(**kwargs)

    def _make_exhausted_result(self, row: RowT) -> ResultT:
        return self.result_cls(
            job_id=self._key_of(row),
            success=False,
            error=(
                f"job exhausted after {getattr(row, 'attempts', '?')} "
                f"attempt(s), last leased by {getattr(row, 'leased_by', '?')}"
            ),
        )

    def _write_result_to_job(self, row: RowT, result: ResultT) -> None:
        for f in dataclasses.fields(self.result_cls):
            if f.name in ("job_id", "success"):
                continue
            if hasattr(row, f.name):
                setattr(row, f.name, getattr(result, f.name))

    def _read_result_from_job(self, row: RowT) -> ResultT:
        kwargs: dict = {
            "job_id": self._key_of(row),
            "success": getattr(row, "status", None) == "completed",
        }
        for f in dataclasses.fields(self.result_cls):
            if f.name in ("job_id", "success"):
                continue
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return self.result_cls(**kwargs)


def new_job_id() -> str:
    """Standard job_id format: 32-char hex (UUID4)."""
    return uuid.uuid4().hex


__all__ = [
    "BaseJobQueue",
    "DEFAULT_LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "INLINE_PUBLISHER",
    "new_job_id",
]
