"""BaseJobQueue — 作业队列基类。

子类提供三个类属性：
- job_model:   ORM 行类
- job_cls:     Job dataclass
- result_cls:  Result dataclass

ORM 列名与 dataclass 字段名一致的自动映射，无需手写钩子。
约定：所有 Result dataclass 必须有 error 字段。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import EngineFactory

DEFAULT_LEASE_SECONDS = 60
MAX_ATTEMPTS = 3

RowT = TypeVar("RowT", bound=Base)
JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


class BaseJobQueue(Generic[RowT, JobT, ResultT]):
    """子类设置 job_model / job_cls / result_cls 即可，零钩子。"""

    job_model: type[RowT]
    job_cls: type[JobT]
    result_cls: type[ResultT]

    def __init__(self, factory: EngineFactory,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self._factory = factory
        self._lease_seconds = lease_seconds

    # -- public ------------------------------------------------------------

    def claim(self, *, worker_id: str) -> JobT | None:
        with self._factory.session() as s:
            row = self._claim(s, worker_id=worker_id)
            s.commit()
            return self._row_to_job(row) if row else None

    def submit_result(self, *, job_id: str, result: ResultT) -> None:
        with self._factory.session() as s:
            self._submit(s, job_id=job_id, result=result)
            s.commit()

    def get_result(self, *, job_id: str) -> ResultT | None:
        with self._factory.session() as s:
            return self._get_result(s, job_id=job_id)

    # -- private (通用逻辑) ------------------------------------------------

    def _claim(self, session: Session, *, worker_id: str) -> RowT | None:
        now = utcnow_naive()
        lease_until = now + timedelta(seconds=self._lease_seconds)

        while True:
            candidate = self._pick_candidate(session, now)
            if candidate is None:
                return None

            if candidate.status == "processing" and candidate.attempts >= MAX_ATTEMPTS:
                exhausted = self._make_exhausted_result(candidate)
                self._submit(session, job_id=candidate.job_id, result=exhausted)
                session.flush()
                continue

            is_reclaim = candidate.status == "processing"
            candidate.status = "processing"
            candidate.leased_by = worker_id
            candidate.leased_until = lease_until
            candidate.attempts += 1
            if not is_reclaim and hasattr(candidate, "started_at"):
                candidate.started_at = now
            return candidate

    def _pick_candidate(self, session: Session, now: datetime) -> RowT | None:
        return session.scalar(
            select(self.job_model)
            .where(or_(
                self.job_model.status == "pending",
                and_(
                    self.job_model.status == "processing",
                    self.job_model.leased_until < now,
                ),
            ))
            .order_by(self.job_model.created_at, self.job_model.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def _submit(self, session: Session, *, job_id: str, result: ResultT) -> None:
        row = session.get(self.job_model, job_id)
        if row is None:
            return
        now = utcnow_naive()
        row.status = "completed" if result.success else "failed"
        if hasattr(row, "completed_at"):
            row.completed_at = now
        self._write_result_to_job(row, result)

    def _get_result(self, session: Session, *, job_id: str) -> ResultT | None:
        row = session.get(self.job_model, job_id)
        if row is None:
            return None
        if row.status not in ("completed", "failed"):
            return None
        return self._read_result_from_job(row)

    # -- 自动映射 (ORM 列名 ↔ dataclass 字段名) --------------------------

    def _row_to_job(self, row: RowT) -> JobT:
        kwargs: dict = {}
        for f in dataclasses.fields(self.job_cls):
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return self.job_cls(**kwargs)

    def _make_exhausted_result(self, row: RowT) -> ResultT:
        return self.result_cls(
            job_id=row.job_id,
            success=False,
            error=f"job exhausted after {row.attempts} attempt(s), last leased by {row.leased_by}",
        )

    def _write_result_to_job(self, row: RowT, result: ResultT) -> None:
        for f in dataclasses.fields(self.result_cls):
            if f.name in ("job_id", "success"):
                continue
            if hasattr(row, f.name):
                setattr(row, f.name, getattr(result, f.name))

    def _read_result_from_job(self, row: RowT) -> ResultT:
        kwargs: dict = {"job_id": row.job_id, "success": row.status == "completed"}
        for f in dataclasses.fields(self.result_cls):
            if f.name in ("job_id", "success"):
                continue
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return self.result_cls(**kwargs)
