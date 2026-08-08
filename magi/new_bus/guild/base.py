"""Job 队列基类。

BaseNotifyBoard -- 单向通知队列（publish 直接落库，不追踪结果）。
BaseJobBoard    -- 往返任务队列（publish → claim → submit_result → get_result）。
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.db.engine import EngineFactory

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

    job_model: type[RowT] | None = None
    job_cls: type[JobT] | None = None
    result_cls: type[ResultT] | None = None
    natural_key_attr: str = "job_id"

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
        """提交结果，key 为 natural_key_attr 的值（如 job_id / event_id）。"""
        with self._session() as s:
            self._submit(s, key=key, result=result)
            s.commit()

    def get_result(self, *, key: str) -> ResultT | None:
        """轮询结果，key 为 natural_key_attr 的值。"""
        with self._session() as s:
            return self._get_result(s, key=key)

    def release(self, *, key: str) -> None:
        """.. deprecated:: 2026-08-08

        Per design §2.6 ("lease：renew，而不是 release 自旋") this
        primitive is **forbidden** for steering hand-off — sequential
        workers would re-claim the same row and multi-worker setups
        cannot share an in-memory ``_active_sessions`` set. New callers
        must use :meth:`renew_lease` for long operations and the
        steering path uses :meth:`chatJobBoard.claim_for_conversation`
        instead of claim-then-release.

        Kept only as a safety valve for the migration period
        (Phase 0–3); Phase 4 will remove it.
        """
        import warnings

        warnings.warn(
            "BaseJobBoard.release() is deprecated; see design §2.6",
            DeprecationWarning,
            stacklevel=2,
        )
        with self._session() as s:
            row = s.scalar(
                select(self.job_model).where(
                    getattr(self.job_model, self.natural_key_attr) == key
                )
            )
            if row is None:
                return
            if row.status == "processing":
                row.status = "pending"
                row.leased_by = None
                row.leased_until = None
                row.attempts = max(0, row.attempts - 1)  # 不消耗重试次数
            s.commit()

    def renew_lease(
        self,
        *,
        key: str,
        owner: str,
        extend_seconds: int | None = None,
    ) -> bool:
        """[claude, 2026-08-08] CAS 续租。

        续约成功返回 ``True``；返回 ``False`` 表示 ownership 已被回收
        （行不存在 / ``leased_by`` 已变 / 状态不再是 ``processing``），
        worker 必须**立即停止**对这一行做任何写入，并放弃本轮结果。

        为什么需要它（设计 §2.6）：Agent 长操作（LLM 120s / tool 300s）
        可超过默认 60s lease。renew 心跳失败 = 别的 worker 已经 reclaim
        = 同一 job 可能有第二个 owner；继续 ``submit_result`` 会覆盖对方
        正在写入的状态，所以失败必须放弃。

        续约是 additive（``leased_until`` 只增不减），避免时钟漂移
        导致续约后 lease 反而缩短。
        """
        import warnings

        warnings.warn(
            "BaseJobBoard.renew_lease is part of the new turn-state machine; "
            "older callers should not rely on it yet.",
            stacklevel=2,
        )
        extend = timedelta(seconds=extend_seconds or self._lease_seconds)
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(
                select(self.job_model).where(
                    getattr(self.job_model, self.natural_key_attr) == key,
                    self.job_model.leased_by == owner,
                    self.job_model.status == "processing",
                )
            )
            if row is None:
                return False
            current = row.leased_until
            # additive: 续约后 lease 至少比当前 leased_until 更长
            new_until = max(
                now + extend,
                current + extend if current is not None else now,
            )
            row.leased_until = new_until
            s.commit()
            return True

    def cancel(
        self,
        *,
        key: str,
        owner: str,
    ) -> bool:
        """[claude, 2026-08-08] Ownership-aware cancel。

        与 ``renew_lease`` 的语义对称：只影响 ``leased_by == owner``
        的行；其他 worker 持有的同名 row 不受影响。

        实现：把 ``leased_until`` 缩到 now()，让 worker 在下次
        ``renew_lease`` 之前感知到自己已失去 ownership。**status
        保持 ``processing``**——任务并没有真正完成，让 lease
        reclaim 自然接管（status 走 ``processing → pending`` →
        别的 worker 的新一轮 claim）。这是设计 §2.6 明确禁止的
        "粗暴改 status=cancelled" 的替代：保留 status 语义
        单一所有权、cancel 只是 lease 提前过期信号。

        不影响调用者对 cancelled 终态的判定——终态由 AgentTurnStore
        的 ``commit_terminal_cancelled()`` 落库（见 agentTurnBook
        设计 §2.2）。
        """
        import warnings

        warnings.warn(
            "BaseJobBoard.cancel is part of the new turn-state machine; "
            "older callers should not rely on it yet.",
            stacklevel=2,
        )
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(
                select(self.job_model).where(
                    getattr(self.job_model, self.natural_key_attr) == key,
                    self.job_model.leased_by == owner,
                    self.job_model.status == "processing",
                )
            )
            if row is None:
                return False
            row.leased_until = now  # 立即过期
            s.commit()
            return True

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
            result = await loop.run_in_executor(
                None, self.get_result, key=key,
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
                self.job_model.status == "pending"
            )
            if channel is not None and hasattr(self.job_model, "channel"):
                stmt = stmt.where(self.job_model.channel == channel)
            return int(s.scalar(stmt) or 0)

    # -- 内部 --------------------------------------------------------------

    def _claim(self, session: Session) -> RowT | None:
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
        _write_result_to_job(row, result, self.result_cls)

    def _get_result(self, session: Session, *, key: str) -> ResultT | None:
        row = session.scalar(
            select(self.job_model).where(
                getattr(self.job_model, self.natural_key_attr) == key
            )
        )
        if row is None or row.status not in ("completed", "failed"):
            return None
        return _read_result_from_job(row, self.result_cls, self.natural_key_attr)

    # -- 键提取 ------------------------------------------------------------

    def _key_of(self, row: RowT) -> str:
        val = getattr(row, self.natural_key_attr, None)
        if val is not None:
            return str(val)
        if hasattr(row, "id"):
            return str(row.id)
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
