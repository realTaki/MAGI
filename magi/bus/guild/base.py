"""Job 队列基类。

BaseNotifyBoard -- 单向通知队列（publish 直接落库，不追踪结果）。
BaseJobBoard    -- 往返任务队列（publish → claim → submit_result → get_result）。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import ClassVar

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import EngineFactory

DEFAULT_LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
#: Hard cap on candidate retries in :meth:`BaseJobBoard._cas_claim`.
#: Bounds the loop when many workers race for the same hot row,
#: matching the chatJobBoard ceiling. Without this cap a hot
#: conversation could spin forever.
MAX_ATTEMPTS_CANDIDATES = 10


class BaseNotifyBoard[JobT]:
    """单向通知队列：publish 直接落库，不追踪结果。

    子类只需 override publish()。
    """

    def __init__(self, factory: EngineFactory):
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def publish(self, job: JobT) -> str:
        raise NotImplementedError


class BaseJobBoard[RowT: Base, JobT, ResultT](BaseNotifyBoard[JobT]):
    """往返任务队列：publish 入队后可通过 claim 认领、submit_result 提交结果、
    get_result 轮询结果，支持租约超时恢复和重试耗尽自动失败。
    """

    # Subclasses MUST set these — there is no default because the
    # abstract ``None`` shape breaks Pylance's view of every ORM
    # call below (``select(self.job_model)`` would otherwise see
    # ``None``). Each concrete Board (``runToolJobBoard``,
    # ``chatJobBoard``, ...) supplies the row / DTO / result
    # types that match its ``Generic[RowT, JobT, ResultT]`` args.
    job_model: ClassVar[type[RowT]]  # type: ignore[reportGeneralTypeIssues]
    job_cls: ClassVar[type[JobT]]  # type: ignore[reportGeneralTypeIssues]
    result_cls: ClassVar[type[ResultT]]  # type: ignore[reportGeneralTypeIssues]
    natural_key_attr: ClassVar[str] = "job_id"
    #: Per-board retry ceiling. Defaults to the global
    #: :data:`MAX_ATTEMPTS` (3). Boards whose domain tolerates more
    #: retries (delivery against flaky channels, chat steering
    #: under load) override this — see
    #: :data:`magi.bus.guild.deliveryJob.MAX_DELIVERY_ATTEMPTS`.
    #: Read by :meth:`_mark_exhausted` so exhaustion failures
    #: respect the same ceiling as the CAS claim loop.
    max_attempts: ClassVar[int] = MAX_ATTEMPTS

    def __init__(self, factory: EngineFactory, lease_seconds: int = DEFAULT_LEASE_SECONDS):
        super().__init__(factory)
        self._lease_seconds = lease_seconds

    # -- ID 生成 -----------------------------------------------------------

    @staticmethod
    def new_job_id() -> str:
        """生成新的 Job ID（hex 字符串）。"""
        import uuid  # local import keeps the base module light and avoids
        # pulling in :mod:`uuid` for callers that never publish.
        return uuid.uuid4().hex

    # -- 异步队列 ----------------------------------------------------------

    def claim(self) -> JobT | None:
        with self._session() as s:
            row = self._claim(s)
            s.commit()
            return _map_row(row, self.job_cls) if row else None

    def submit_result(self, *, key: str, result: ResultT) -> None:
        """提交结果，key 为 natural_key_attr 的值（即 job_id）。"""
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
                select(self.job_model).where(getattr(self.job_model, self.natural_key_attr) == key)
            )
            if row is None:
                return
            if getattr(row, "status", None) == "processing":
                row.status = "pending"  # type: ignore[reportAttributeAccessIssue]
                row.leased_by = None  # type: ignore[reportAttributeAccessIssue]
                row.leased_until = None  # type: ignore[reportAttributeAccessIssue]
                row.attempts = max(0, getattr(row, "attempts", 0) - 1)  # type: ignore[reportAttributeAccessIssue]  # 不消耗重试次数
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
            # ``get_result``'s ``key`` is keyword-only, so we can't
            # pass it positionally through ``run_in_executor``'s
            # ``*args``. Wrap in a lambda so Pylance sees a no-arg
            # callable and the runtime forwards ``key=key`` correctly.
            result = await loop.run_in_executor(
                None,
                lambda: self.get_result(key=key),
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
            stmt = (
                select(func.count())
                .select_from(self.job_model)
                .where(self.job_model.status == "pending")  # type: ignore[reportAttributeAccessIssue]
            )
            if channel is not None and hasattr(self.job_model, "channel"):
                stmt = stmt.where(self.job_model.channel == channel)  # type: ignore[reportAttributeAccessIssue]
            return int(s.scalar(stmt) or 0)

    # -- 内部 --------------------------------------------------------------

    def _claim(self, session: Session) -> RowT | None:
        """Default CAS-claim — no extra WHERE filter.

        Specialized boards (``deliveryJobBoard.claim_for_channel``,
        ``chatJobBoard.claim_for_conversation``) wrap
        :meth:`_cas_claim` with their own scoping clause.
        """
        return self._cas_claim(
            session,
            owner=f"worker:{self.__class__.__name__}:{id(self)}",
        )

    def _cas_claim(
        self,
        session: Session,
        *,
        owner: str,
        extra_where: list[ColumnElement[bool]] | None = None,
    ) -> RowT | None:
        """Shared CAS-claim loop — find candidate, conditional UPDATE, check rowcount.

        Replaces the previous ``SELECT ... FOR UPDATE SKIP LOCKED`` path,
        which SQLite silently no-ops under WAL — multiple consumers
        would happily read the same "locked" row, leading to duplicate
        execution and ``release`` churn on delivery boards. The CAS
        pattern (find candidate → conditional UPDATE → check rowcount)
        gives us row-level atomicity with a single SQL statement;
        ``rowcount == 1`` means we own the row, ``0`` means another
        worker grabbed it first.

        ``extra_where`` lets callers narrow the candidate pool
        without subclassing — delivery passes ``channel=...``,
        chat passes ``conversation_id=...``. The same clause is
        applied to the candidate SELECT *and* the CAS UPDATE so
        the row can't drift out of scope between the two reads.

        ``owner`` is the string written to the ``leased_by`` column
        (when the row has one). Pass a process-local, board-local
        tag so lease diagnostics can attribute which worker is
        currently holding each row.

        The loop is bounded by :data:`MAX_ATTEMPTS_CANDIDATES` so a
        hot row doesn't spin forever; on exhaustion we return
        ``None`` and let the caller decide what to do (retry on
        the next poll, alert, etc.).
        """
        has_leased_by = hasattr(self.job_model, "leased_by")
        has_started_at = hasattr(self.job_model, "started_at")
        now = utcnow_naive()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        for _ in range(MAX_ATTEMPTS_CANDIDATES):
            candidate = self._pick_candidate(
                session,
                now,
                extra_where=extra_where,
            )
            if candidate is None:
                return None
            status = getattr(candidate, "status", "")
            attempts = getattr(candidate, "attempts", 0)
            # The candidate read is deliberately lock-free, therefore
            # exhaustion must also be a conditional update. Another worker
            # may have completed or renewed this lease after our read; do
            # not overwrite that newer state with ``failed``.
            if status == "processing" and attempts >= self.max_attempts:
                if not self._mark_exhausted(
                    session,
                    candidate,
                    now=now,
                    extra_where=extra_where,
                ):
                    session.rollback()
                continue
            is_reclaim = status == "processing"
            # Conditional UPDATE: same row, same status / lease invariants
            # — atomic on SQLite. rowcount == 1 ⇒ we own the row; 0 ⇒
            # another worker grabbed it first; retry with the next
            # candidate.
            invariant = or_(
                self.job_model.status == "pending",  # type: ignore[reportAttributeAccessIssue]
                and_(
                    self.job_model.status == "processing",  # type: ignore[reportAttributeAccessIssue]
                    self.job_model.leased_until < now,  # type: ignore[reportAttributeAccessIssue]
                ),
            )
            where_clauses: list[ColumnElement[bool]] = [
                self.job_model.id == candidate.id,  # type: ignore[reportAttributeAccessIssue]
                invariant,
            ]
            if extra_where:
                where_clauses.extend(extra_where)
            values: dict = {
                "status": "processing",
                "leased_until": lease_until,
                "attempts": attempts + 1,
            }
            if has_leased_by:
                values["leased_by"] = owner
            if not is_reclaim and has_started_at:
                values["started_at"] = now
            result = session.execute(update(self.job_model).where(*where_clauses).values(**values))
            if getattr(result, "rowcount", 0) == 1:
                # Reload the fresh row so the caller sees the
                # post-UPDATE values (leased_until, attempts, …).
                fresh = session.get(self.job_model, candidate.id)  # type: ignore[reportAttributeAccessIssue]
                return fresh
            # Lost the race — try the next candidate.
            session.rollback()
            now = utcnow_naive()
            lease_until = now + timedelta(seconds=self._lease_seconds)
        return None

    def _pick_candidate(
        self,
        session: Session,
        now: datetime,
        extra_where: list[ColumnElement[bool]] | None = None,
    ) -> RowT | None:
        """Pick the oldest pending / lease-expired row WITHOUT a lock.

        The lock is the conditional UPDATE in :meth:`_cas_claim`.
        This SELECT is purely for ordering and filtering — a stale
        read here is harmless because the UPDATE re-validates the
        row's status / lease invariants.

        ``extra_where`` (if supplied) is appended to the same
        ``status / lease`` invariant so callers (delivery, chat)
        can scope the candidate pool to their slice.
        """
        where_clauses: list[ColumnElement[bool]] = [
            or_(
                self.job_model.status == "pending",  # type: ignore[reportAttributeAccessIssue]
                and_(
                    self.job_model.status == "processing",  # type: ignore[reportAttributeAccessIssue]
                    self.job_model.leased_until < now,  # type: ignore[reportAttributeAccessIssue]
                ),
            ),
        ]
        if extra_where:
            where_clauses.extend(extra_where)
        return session.scalar(
            select(self.job_model)
            .where(*where_clauses)
            .order_by(
                self.job_model.created_at,  # type: ignore[reportAttributeAccessIssue]
                self.job_model.id,  # type: ignore[reportAttributeAccessIssue]
            )
            .limit(1)
        )

    def _mark_exhausted(
        self,
        session: Session,
        candidate: RowT,
        *,
        now: datetime,
        extra_where: list[ColumnElement[bool]] | None,
    ) -> bool:
        """Atomically fail an expired lease that consumed all attempts.

        Honours :attr:`max_attempts` so boards with a higher
        per-board ceiling (e.g. ``deliveryJobBoard`` with
        :data:`~magi.bus.guild.deliveryJob.MAX_DELIVERY_ATTEMPTS = 10`)
        fail rows at their own threshold, not the generic 3.
        """
        result = self._make_exhausted_result(candidate)
        values: dict[str, object] = {"status": "failed"}
        if hasattr(self.job_model, "completed_at"):
            values["completed_at"] = now
        for field in dataclasses.fields(self.result_cls):  # type: ignore[reportArgumentType]
            if field.name != "success" and hasattr(self.job_model, field.name):
                values[field.name] = getattr(result, field.name)

        where_clauses: list[ColumnElement[bool]] = [
            self.job_model.id == candidate.id,  # type: ignore[reportAttributeAccessIssue]
            self.job_model.status == "processing",  # type: ignore[reportAttributeAccessIssue]
            self.job_model.attempts >= self.max_attempts,  # type: ignore[reportAttributeAccessIssue]
            self.job_model.leased_until < now,  # type: ignore[reportAttributeAccessIssue]
        ]
        if extra_where:
            where_clauses.extend(extra_where)
        update_result = session.execute(
            update(self.job_model).where(*where_clauses).values(**values)
        )
        return getattr(update_result, "rowcount", 0) == 1

    def _submit(self, session: Session, *, key: str, result: ResultT) -> None:
        row = session.scalar(
            select(self.job_model).where(getattr(self.job_model, self.natural_key_attr) == key)
        )
        if row is None:
            return
        now = utcnow_naive()
        row.status = "completed" if result.success else "failed"  # type: ignore[reportAttributeAccessIssue]
        if hasattr(row, "completed_at"):
            row.completed_at = now  # type: ignore[reportAttributeAccessIssue]
        _write_result_to_job(row, result, self.result_cls)

    def _get_result(self, session: Session, *, key: str) -> ResultT | None:
        row = session.scalar(
            select(self.job_model).where(getattr(self.job_model, self.natural_key_attr) == key)
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
            return str(row.id)  # type: ignore[reportAttributeAccessIssue]
        return ""

    # -- 耗尽处理 ----------------------------------------------------------

    def _make_exhausted_result(self, row: RowT) -> ResultT:
        return _make_exhausted_result(row, self.result_cls, self.natural_key_attr)


# -- 模块级映射工具 ----------------------------------------------------------


def _map_row(row, cls):
    """ORM 行 → dataclass 自动映射（按字段名匹配）。"""
    kwargs = {}
    for f in dataclasses.fields(cls):
        if hasattr(row, f.name):
            kwargs[f.name] = getattr(row, f.name)
    return cls(**kwargs)


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
        kwargs["error"] = f"job exhausted after {row.attempts} attempt(s)"
    return result_cls(**kwargs)
