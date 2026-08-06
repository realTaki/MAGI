"""BaseJob — write primitive for the BUS.

Each Job has its own ORM class (per the new_bus convention: "每个
Job 和 Book 都有其ORM").  Job ORM classes live in
``JobBase`` — a **separate** ``DeclarativeBase`` from
``magi.new_bus.db.base.Base`` so two ORM classes declaring the same
``__tablename__`` (one in a Book, one in a Job) don't collide on
SQLAlchemy's Table registry.

The split between Books and Jobs implements CQRS:

- :mod:`magi.new_bus.books` — **reads** (get / list / search).
- :mod:`magi.new_bus.jobs`  — **writes** (create / update / delete /
  mark_* / publish).  Each write can go either directly to the
  table (synchronous path) or via a Queue (asynchronous path)
  depending on whether the caller injected a Queue dependency.

If no other module needs to coordinate on the write, the
sync path is enough — just instantiate the Job with an
``EngineFactory``.  If multiple modules need to react to the same
write (e.g. one publishes, another consumes via
``claim() / submit_result()``), the caller injects a
:class:`magi.new_bus.queues.BaseJobQueue` and the Job publishes
to it.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import DeclarativeBase, Session

from magi.new_bus.db.engine import EngineFactory


def job_utcnow_naive() -> datetime:
    """Naive UTC helper for Job ORM defaults."""
    return datetime.now(UTC).replace(tzinfo=None)


class JobBase(DeclarativeBase):
    """Separate declarative base for Job ORM classes.

    Book ORM classes use :class:`magi.new_bus.db.base.Base`; Job
    ORM classes use this one.  The two Bases have **independent**
    MetaData instances, so ORM classes with the same
    ``__tablename__`` (one in a Book, one in a Job) don't collide.

    The actual SQLite file is shared, so SQL tables are still
    physically identical; SQLAlchemy just doesn't see both
    Tables in one MetaData.
    """


# -- Async-job infrastructure --------------------------------------------
#
# A Job that wants the async path sets three class attributes:
#   job_model, job_cls, result_cls
# and inherits claim / submit_result / get_result / recover_expired_leases
# from BaseJob below.  Jobs that don't set them only have the
# synchronous path (direct DB writes).


DEFAULT_LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
INLINE_PUBLISHER = "__inline__"

RowT = TypeVar("RowT", bound=JobBase)
JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


class BaseJob(Generic[RowT, JobT, ResultT]):
    """Base for all new_bus write jobs.

    Subclasses set ``job_model`` / ``job_cls`` / ``result_cls`` and
    implement ``publish``.  Optionally ``_run_inline`` if they
    support ``inline=True`` publish.

    Subclass contract
    -----------------

    - ``job_model`` — ORM class for the queue/wait-table
    - ``job_cls``   — public ``Job`` dataclass (publisher input)
    - ``result_cls`` — public ``Result`` dataclass (worker output,
      must have ``job_id`` + ``success``)
    - ``publish(job, *, inline=False, **kwargs) -> str`` — insert
      a row, optionally run inline, return natural key
    """

    job_model: type[RowT]
    job_cls: type[JobT]
    result_cls: type[ResultT]
    natural_key_attr: str = "job_id"

    def __init__(
        self,
        factory: EngineFactory,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ):
        self._factory = factory
        self._lease_seconds = lease_seconds

    def _session(self):
        return self._factory.session()

    # -- public: lifecycle ------------------------------------------------

    def publish(self, job: JobT, *, inline: bool = False, **kwargs) -> str:
        """Insert a job row; return its natural key.

        Subclass-implemented.  Default raises ``NotImplementedError``
        so a Job that doesn't set ``job_model`` (purely-sync Job)
        can override ``publish`` directly to do a table insert.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.publish must be implemented"
        )

    def claim(self, *, worker_id: str) -> JobT | None:
        """Claim the next pending or expired-lease job for ``worker_id``."""
        if not getattr(self, "job_model", None):
            return None
        with self._factory.session() as s:
            row = self._claim(s, worker_id=worker_id)
            s.commit()
            return self._row_to_job(row) if row else None

    def submit_result(self, *, job_id: str, result: ResultT) -> None:
        if not getattr(self, "job_model", None):
            return
        with self._factory.session() as s:
            self._submit(s, job_id=job_id, result=result)
            s.commit()

    def get_result(self, *, job_id: str) -> ResultT | None:
        if not getattr(self, "job_model", None):
            return None
        with self._factory.session() as s:
            return self._get_result(s, job_id=job_id)

    def recover_expired_leases(self) -> int:
        if not getattr(self, "job_model", None):
            return 0
        now = job_utcnow_naive()
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

    def _run_inline(self, session: Session, *, job_id: str, **kwargs) -> ResultT:
        """Subclass OPTIONAL: synchronous work for ``inline=True`` publish."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support inline=True"
        )

    # -- private: standard claim/submit machinery -----------------------

    def _claim(self, session: Session, *, worker_id: str) -> RowT | None:
        now = job_utcnow_naive()
        from datetime import timedelta
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
                self._submit(
                    session,
                    job_id=self._key_of(candidate),
                    result=exhausted,
                )
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
        now = job_utcnow_naive()
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


__all__ = [
    "JobBase",
    "BaseJob",
    "DEFAULT_LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "INLINE_PUBLISHER",
    "job_utcnow_naive",
]
