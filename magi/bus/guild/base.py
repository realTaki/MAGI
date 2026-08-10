"""Job 队列基类。

BaseNotifyBoard -- 单向通知队列（publish 直接落库，不追踪结果）。
BaseJobBoard    -- 往返任务队列（publish → claim → submit_result → get_result）。
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import ClassVar, Generic, TypeVar

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import EngineFactory

DEFAULT_LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


def new_job_id() -> str:
    """生成新的 Job ID（hex 字符串）。"""
    return uuid.uuid4().hex


RowT = TypeVar("RowT", bound=Base)
JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


class BaseNotifyBoard(Generic[JobT]):
    """单向通知队列：publish 直接落库，不追踪结果。

    子类只需 override publish()。
    """

    def __init__(self, factory: EngineFactory):
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def publish(self, job: JobT) -> str:
        raise NotImplementedError


class BaseJobBoard(BaseNotifyBoard[JobT], Generic[RowT, JobT, ResultT]):
    """往返任务队列：publish 入队后可通过 claim 认领、submit_result 提交结果、
    get_result 轮询结果，支持租约超时恢复和重试耗尽自动失败。
    """

    # Subclasses MUST set these — there is no default because the
    # abstract ``None`` shape breaks Pylance's view of every ORM
    # call below (``select(self.job_model)`` would otherwise see
    # ``None``). Each concrete Board (``runToolJobBoard``,
    # ``chatJobBoard``, ...) supplies the row / DTO / result
    # types that match its ``Generic[RowT, JobT, ResultT]`` args.
    job_model: ClassVar[type[RowT]]
    job_cls: ClassVar[type[JobT]]
    result_cls: ClassVar[type[ResultT]]
    natural_key_attr: ClassVar[str] = "job_id"

    def __init__(self, factory: EngineFactory,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS):
        super().__init__(factory)
        self._lease_seconds = lease_seconds

    # -- 异步队列 ----------------------------------------------------------

    def claim(self) -> JobT | None:
        with self._session() as s:
            row = self._claim(s)
            s.commit()
            return _row_to_job(row, self.job_cls) if row else None

    def submit_result(self, *, key: str, result: ResultT) -> None:
        """提交结果，key 为 natural_key_attr 的值（如 job_id / invocation_id）。"""
        with self._session() as s:
            self._submit(s, key=key, result=result)
            s.commit()

    def get_result(self, *, key: str) -> ResultT | None:
        """轮询结果，key 为 natural_key_attr 的值。"""
        with self._session() as s:
            return self._get_result(s, key=key)

    def release(self, *, key: str) -> None:
        """Release a claimed job back to *pending*.

        Used by AgentWorker when ``_run()`` claims a ChatJob for a
        session that already has an active in-flight run.  The job
        is released so ``_process()`` can reclaim it as steering
        via ``claim_for_conversation``.
        """
        with self._session() as s:
            row = s.scalar(
                select(self.job_model).where(
                    getattr(self.job_model, self.natural_key_attr) == key
                )
            )
            if row is None:
                return
            if getattr(row, "status", None) == "processing":
                setattr(row, "status", "pending")
                setattr(row, "leased_by", None)
                setattr(row, "leased_until", None)
                setattr(row, "attempts", max(0, getattr(row, "attempts", 0) - 1))  # 不消耗重试次数
            s.commit()

    async def wait_for_result(
        self,
        *,
        key: str,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
    ) -> ResultT | None:
        """Block until the worker submits a result for *key* or *timeout* elapses.

        Useful for callers that need to confirm a write reached
        the durable side before reporting success to the LLM /
        API (e.g. :mod:`magi.tools.mcp` waits for
        :class:`~magi.mcp.worker.McpWorker` to finish upserting
        a row before returning). Returns ``None`` on timeout so
        the caller can surface "the worker hasn't answered yet"
        as a distinct failure mode from "the worker said no".

        The DB read runs in a thread so the event loop stays
        responsive while the Worker — which polls every
        ~0.25s — catches up.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            # ``run_in_executor`` takes ``*args`` positionally; the
            # previous ``key=key`` shape raised Pylance's
            # ``reportCallIssue`` AND would TypeError at runtime.
            result = await loop.run_in_executor(
                None, self.get_result, key,
            )
            if result is not None:
                return result
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(poll_interval)

    # -- 队列深度 ----------------------------------------------------------

    def pending_count(self, *, channel: str | None = None) -> int:
        """Count rows in pending state, optionally filtered by ``channel``.

        Used by ChannelWorker._claim_delivery_loop for backpressure.
        """
        with self._session() as s:
            stmt = select(func.count()).select_from(self.job_model).where(
                getattr(self.job_model, "status") == "pending"
            )
            if channel is not None and hasattr(self.job_model, "channel"):
                stmt = stmt.where(getattr(self.job_model, "channel") == channel)
            return int(s.scalar(stmt) or 0)

    # -- 内部 --------------------------------------------------------------

    def _claim(self, session: Session) -> RowT | None:
        now = utcnow_naive()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        while True:
            candidate = self._pick_candidate(session, now)
            if candidate is None:
                return None
            status = getattr(candidate, "status", "")
            attempts = getattr(candidate, "attempts", 0)
            if status == "processing" and attempts >= MAX_ATTEMPTS:
                exhausted = self._make_exhausted_result(candidate)
                self._submit(session, key=self._key_of(candidate), result=exhausted)
                session.flush()
                continue
            is_reclaim = status == "processing"
            setattr(candidate, "status", "processing")
            setattr(candidate, "leased_until", lease_until)
            setattr(candidate, "attempts", attempts + 1)
            if not is_reclaim and hasattr(candidate, "started_at"):
                setattr(candidate, "started_at", now)
            return candidate

    def _pick_candidate(self, session: Session, now: datetime) -> RowT | None:
        return session.scalar(
            select(self.job_model)
            .where(or_(
                getattr(self.job_model, "status") == "pending",
                and_(
                    getattr(self.job_model, "status") == "processing",
                    getattr(self.job_model, "leased_until") < now,
                ),
            ))
            .order_by(
                getattr(self.job_model, "created_at"),
                getattr(self.job_model, "id"),
            )
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
        setattr(row, "status", "completed" if getattr(result, "success") else "failed")
        if hasattr(row, "completed_at"):
            setattr(row, "completed_at", now)
        _write_result_to_job(row, result, self.result_cls)

    def _get_result(self, session: Session, *, key: str) -> ResultT | None:
        row = session.scalar(
            select(self.job_model).where(
                getattr(self.job_model, self.natural_key_attr) == key
            )
        )
        if row is None or getattr(row, "status", "") not in ("completed", "failed"):
            return None
        return _read_result_from_job(row, self.result_cls, self.natural_key_attr)

    # -- 键提取 ------------------------------------------------------------

    def _key_of(self, row: RowT) -> str:
        val = getattr(row, self.natural_key_attr, None)
        if val is not None:
            return str(val)
        if hasattr(row, "id"):
            return str(getattr(row, "id"))
        return ""

    # -- 耗尽处理 ----------------------------------------------------------

    def _make_exhausted_result(self, row: RowT) -> ResultT:
        return _make_exhausted_result(
            row, self.result_cls, self.natural_key_attr
        )


# -- 模块级映射工具 ----------------------------------------------------------


def _map_row(row, cls):
    """ORM 行 → dataclass 自动映射（按字段名匹配）。"""
    kwargs = {}
    for f in dataclasses.fields(cls):
        if hasattr(row, f.name):
            kwargs[f.name] = getattr(row, f.name)
    return cls(**kwargs)


def _row_to_job(row, job_cls):
    return _map_row(row, job_cls)


def _write_result_to_job(row, result, result_cls) -> None:
    """将 result dataclass 的字段写回 ORM 行（跳过业务键和 success）。"""
    for f in dataclasses.fields(result_cls):
        if f.name in ("success",):
            continue
        if hasattr(row, f.name):
            setattr(row, f.name, getattr(result, f.name))


def _read_result_from_job(row, result_cls, natural_key_attr: str):
    """从 ORM 行重建 result dataclass。"""
    key_val = getattr(row, natural_key_attr, None)
    key_val = str(key_val) if key_val is not None else ""
    kwargs: dict = {
        natural_key_attr: key_val,
        "success": row.status == "completed",
    }
    for f in dataclasses.fields(result_cls):
        if f.name in ("success", natural_key_attr):
            continue
        if hasattr(row, f.name):
            kwargs[f.name] = getattr(row, f.name)
    return result_cls(**kwargs)


def _make_exhausted_result(row, result_cls, natural_key_attr: str):
    """构造一个"重试耗尽"的失败 Result。"""
    key_val = getattr(row, natural_key_attr, None)
    key_val = str(key_val) if key_val is not None else ""
    kwargs: dict = {
        natural_key_attr: key_val,
        "success": False,
    }
    if hasattr(row, "attempts"):
        kwargs["error"] = (
            f"job exhausted after {row.attempts} attempt(s)"
        )
    return result_cls(**kwargs)
