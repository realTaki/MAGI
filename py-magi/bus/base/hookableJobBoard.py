"""Claimable JobBoard for work that another Worker must pick up."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from sqlalchemy import select

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from .engine import EngineFactory
from .go import go, wait


type PostPublishHook[JobT: BaseJob, ResultT: BaseJobResult] = Callable[[JobT], ResultT]
type PostResultHook[ResultT: BaseJobResult] = Callable[[ResultT], None]


class HookableJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """Cross-worker JobBoard: publish, hooks, claim, submit.

    ``publish`` writes PREPARING, runs :meth:`_prepare`, then the post-publish
    hooks. Successful hooks move the Job to PENDING so a Worker can
    :meth:`claim` it. :meth:`submit_result` is first-result-wins and then
    runs post-result hooks.
    """

    def __init__(self, factory: EngineFactory) -> None:
        super().__init__(factory)
        self._post_publish_hooks: list[PostPublishHook[JobT, ResultT]] = []
        self._post_result_hooks: list[PostResultHook[ResultT]] = []

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        published = replace(job, id=job_id)
        self._prepare(published)
        go(self._post_publish(published))
        return job_id

    def _prepare(self, job: JobT) -> None:
        """Persist board-owned side effects before the Job becomes claimable."""
        del job

    async def _post_publish(self, job: JobT) -> JobStatus:
        gathered = await wait(self._post_publish_hooks, job)
        failed = any(item.status is JobStatus.FAILED for item in gathered)
        status = JobStatus.FAILED if failed else JobStatus.PENDING
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job.id)
            row.status = status.value
            if failed:
                row.error = "\n".join(item.error for item in gathered if item.error)
            session.commit()
        return status

    def claim(self) -> JobT | None:
        row_cls = type(self).row_cls
        with self._session() as session:
            row = session.scalar(
                select(row_cls)
                .where(row_cls.status == JobStatus.PENDING.value)
                .order_by(row_cls.created_at, row_cls.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = JobStatus.CLAIMED.value
            session.commit()
            return cast(type[JobT], self.job_cls).from_row(row)

    def list(self, *, status: JobStatus | None = None) -> list[JobT]:
        row_cls = type(self).row_cls
        stmt = select(row_cls).order_by(row_cls.created_at, row_cls.id)
        if status is not None:
            stmt = stmt.where(row_cls.status == status.value)
        with self._session() as session:
            return [cast(type[JobT], self.job_cls).from_row(row) for row in session.scalars(stmt)]

    def submit_result(self, result: BaseJobResult) -> bool:
        """Persist the first result only; later submissions are rejected."""
        with self._session() as session:
            row = session.get(type(self).row_cls, result.id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            self._write_result(row, result)
            session.commit()
        go(self._post_result(cast(ResultT, result)))
        return True

    async def _post_result(self, result: ResultT) -> None:
        for hook in self._post_result_hooks:
            go(asyncio.to_thread(hook, result))
