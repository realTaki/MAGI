"""BaseJobQueue — Job 基类，同步和异步共用。

同步 Job: 继承后只 override publish()，直接落库。
异步 Job: 设置 job_model/job_cls/result_cls，启用 claim/submit_result/get_result。
每张表通过 natural_key_attr 声明自己的业务键名（默认 "job_id"）。
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

    # 异步 Job 设置这四个
    job_model: type[RowT] | None = None
    job_cls: type[JobT] | None = None
    result_cls: type[ResultT] | None = None
    #: ORM 列名，作为业务键（默认 "job_id"），如 "attempt_id"、"delivery_id"
    natural_key_attr: str = "job_id"

    def __init__(self, factory: EngineFactory,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self._factory = factory
        self._lease_seconds = lease_seconds

    def _session(self):
        return self._factory.session()

    # -- publish (子类 override) -------------------------------------------

    def publish(self, job: JobT) -> str:
        raise NotImplementedError

    # -- 异步队列 (需设置 job_model/job_cls/result_cls) -------------------

    def claim(self, *, worker_id: str) -> JobT | None:
        with self._session() as s:
            row = self._claim(s, worker_id=worker_id)
            s.commit()
            return self._row_to_job(row) if row else None

    def submit_result(self, *, key: str, result: ResultT) -> None:
        """提交结果，key 为 natural_key_attr 的值（如 job_id / attempt_id）。"""
        with self._session() as s:
            self._submit(s, key=key, result=result)
            s.commit()

    def get_result(self, *, key: str) -> ResultT | None:
        """轮询结果，key 为 natural_key_attr 的值。"""
        with self._session() as s:
            return self._get_result(s, key=key)

    # -- 内部 --------------------------------------------------------------

    def _claim(self, session: Session, *, worker_id: str) -> RowT | None:
        now = utcnow_naive()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        while True:
            candidate = self._pick_candidate(session, now)
            if candidate is None:
                return None
            if candidate.status == "processing" and candidate.attempts >= MAX_ATTEMPTS:
                exhausted = self._make_exhausted_result(candidate)
                self._submit(session, key=self._key_of(candidate), result=exhausted)
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
                and_(self.job_model.status == "processing", self.job_model.leased_until < now),
            ))
            .order_by(self.job_model.created_at, self.job_model.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def _submit(self, session: Session, *, key: str, result: ResultT) -> None:
        row = session.scalar(
            select(self.job_model).where(
                getattr(self.job_model, self.natural_key_attr) == key
            )
        )
        if row is None:
            return
        now = utcnow_naive()
        row.status = "completed" if result.success else "failed"
        if hasattr(row, "completed_at"):
            row.completed_at = now
        self._write_result_to_job(row, result)

    def _get_result(self, session: Session, *, key: str) -> ResultT | None:
        row = session.scalar(
            select(self.job_model).where(
                getattr(self.job_model, self.natural_key_attr) == key
            )
        )
        if row is None or row.status not in ("completed", "failed"):
            return None
        return self._read_result_from_job(row)

    # -- 键提取 ------------------------------------------------------------

    def _key_of(self, row: RowT) -> str:
        val = getattr(row, self.natural_key_attr, None)
        if val is not None:
            return str(val)
        # fallback: 用 PK id
        if hasattr(row, "id"):
            return str(row.id)
        return ""

    # -- 自动映射 (ORM 列名 ↔ dataclass 字段名) --------------------------

    def _row_to_job(self, row: RowT) -> JobT:
        return self._map_row(row, self.job_cls)

    def _make_exhausted_result(self, row: RowT) -> ResultT:
        return self.result_cls(
            job_id=self._key_of(row),
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
        kwargs: dict = {"job_id": self._key_of(row), "success": row.status == "completed"}
        for f in dataclasses.fields(self.result_cls):
            if f.name in ("job_id", "success"):
                continue
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return self.result_cls(**kwargs)

    @staticmethod
    def _map_row(row, cls):
        kwargs = {}
        for f in dataclasses.fields(cls):
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return cls(**kwargs)
