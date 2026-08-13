"""Job 队列基类。

BaseJobBoard -- 往返任务队列（publish → claim → submit_result → get_result）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import DateTime, Integer, String, and_, func, or_, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from magi.bus.db.base import Base, enum_column, utcnow_naive
from magi.bus.db.engine import EngineFactory

DEFAULT_LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
#: Hard cap on candidate retries in :meth:`BaseJobBoard._cas_claim`.
#: Bounds the loop when many workers race for the same hot row,
#: matching the chatJobBoard ceiling. Without this cap a hot
#: conversation could spin forever.
MAX_ATTEMPTS_CANDIDATES = 10


# -- 公共基类 / 列 mixin ---------------------------------------------------


class JobStatus(StrEnum):
    """Job 队列状态机 + 业务终态。

    Row 层（``BaseJobRowMixin.status``）承载全部 4 个值；Result 层
    （``BaseJobResult.status``）只承载终态子集 :attr:`COMPLETED` /
    :attr:`FAILED`，因为 Result 只在 worker submit 时构造。

    :class:`~enum.StrEnum`——成员继承 ``str``，``JobStatus.COMPLETED ==
    "completed"`` 恒为真、JSON 序列化直接输出 ``"completed"``。存储走
    :func:`magi.bus.db.base.enum_column`（``values_callable`` 把存储
    / CHECK 锁定到 ``.value``），业务代码比较用 ``JobStatus.COMPLETED``
    而不是字符串字面量。
    """

    PENDING = "pending"        # 入队未 claim
    PROCESSING = "processing"  # 已 claim，worker 处理中
    COMPLETED = "completed"    # 业务成功（Result 视角 = SUCCEEDED）
    FAILED = "failed"          # 业务失败 / 重试耗尽 / 过期


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseJob:
    """所有 Job dataclass 的公共基类。

    承载队列语义字段（``job_id`` / ``attempts``）；子类只声明
    业务字段。``job_id`` 由 ``publish`` 自动生成（``job.job_id
    or new_job_id()``），``attempts`` 由 :meth:`BaseJobBoard._map_row`
    在 claim 时回填，供上层观察 lease-recovery 行为，不属于
    publisher 的输入。

    ``kw_only=True`` 让这两个队列语义字段排到子类业务字段之后，
    这样子类可以声明无默认值的必填字段（如 ``DeliveryJob.channel``）
    而不违反 dataclass「无默认字段不能跟在有默认字段之后」的规则。
    """

    job_id: str = ""  # 发布时自动生成；publish 内 ``job.job_id or new_job_id()``
    attempts: int = 0  # claim 后由 ORM 回填的重试次数（观察值）


@dataclass(frozen=True, slots=True)
class BaseJobResult:
    """所有 Result dataclass 的公共基类。

    承载队列语义字段：``job_id``（业务键）、``status``
    （Result 业务终态，取 :class:`JobStatus` 子集
    :attr:`JobStatus.COMPLETED` / :attr:`JobStatus.FAILED`，由
    :meth:`BaseJobBoard._read_result_from_job` 从 row.status
    归一化）、``error``（失败时的人类可读描述；成功路径下
    Result 通常不构造，走过 :meth:`BaseJobBoard._submit` 的快
    路径时 ``error`` 保持 ``None``）。子类只声明纯业务字段,
    走 :meth:`BaseJobBoard._write_result_to_job` /
    :meth:`BaseJobBoard._read_result_from_job` 的通用字段映射。
    这样「队列语义 vs 业务字段」的边界显式化，子类不再需要
    各自抄一遍 ``job_id`` / ``status`` / ``error``。

    三个字段都带默认值，以兼容「无参构造后检查业务字段默认值」
    的用法（如 ``A2ARequestResult().error_code is None``）。

    .. note::
       :class:`~magi.bus.guild.chatJob.ChatJobResult` 不继承
       ``error`` 字段，它用
       :attr:`~magi.bus.guild.chatJob.ChatJobResult.error_detail`
       表达同一概念（语义同源但历史命名未统一）。这条注释是给
       后续想 ``grep "error:"`` 字段的人准备的。
    """

    job_id: str = ""  # 对应 job 的 natural_key_attr 值（默认 "job_id"）
    status: JobStatus = JobStatus.COMPLETED  # Result 业务终态（PENDING 由 Result 视角不承载）
    error: str | None = None  # 失败时的人类可读错误文案（成功路径保持 None）


class BaseJobRowMixin:
    """Job 队列行的公共列 mixin。

    每个 ``_XxxJobRow`` 通过 ``class _XxxJobRow(BaseJobRowMixin, Base)``
    继承这 10 个队列控制列，只声明自己的业务列。
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Native enum column — see :class:`JobStatus` docstring. The full SAEnum
    # configuration (values_callable / length / create_constraint / name)
    # lives in :func:`magi.bus.db.base.enum_column` so all 10 Job boards
    # share one source of truth. ``name="job_status"`` is the PG
    # ``CREATE TYPE`` / SQLite CHECK constraint label emitted by the
    # collapsed initial schema (see
    # :mod:`magi.bus.db.alembic.versions.0001_initial_schema` and
    # :mod:`magi.bus.db.alembic.magis_versions.0001_initial_schema` —
    # the 2026.08 dev-mode collapse folded the historical migration
    # chain into a single baseline per scope).
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, name="job_status"),
        nullable=False,
        default=JobStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BaseJobBoard[RowT: Base, JobT: BaseJob, ResultT: BaseJobResult]:
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
        self._factory = factory
        self._lease_seconds = lease_seconds

    def _session(self):
        return self._factory.session()

    # -- ID 生成 -----------------------------------------------------------

    @staticmethod
    def new_job_id() -> str:
        """生成新的 Job ID（hex 字符串）。"""
        import uuid  # local import keeps the base module light and avoids
        # pulling in :mod:`uuid` for callers that never publish.
        return uuid.uuid4().hex

    @staticmethod
    def _map_row(row, cls):
        """ORM 行 → dataclass 自动映射（按字段名匹配）。"""
        kwargs = {}
        for f in dataclasses.fields(cls):
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return cls(**kwargs)

    # -- 异步队列 ----------------------------------------------------------

    def publish(self, job: JobT) -> str:
        """Enqueue a new PENDING row and return its freshly minted ``job_id``.

        Default impl: copy every dataclass field whose name is also a
        column on :attr:`job_model` (skipping ``job_id`` /
        ``attempts`` which :class:`BaseJob` owns), insert, commit.

        Subclasses that need pre-insert side effects (cross-channel
        guard, derived columns, idempotency lookup, external
        settings write) override :meth:`publish` and call
        :meth:`_build_pending_row` after their checks — the
        dataclass→row copy itself stays generic.
        """
        with self._session() as s:
            row = self._build_pending_row(job)
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id

    def _build_pending_row(self, job: JobT) -> RowT:
        """Build a new PENDING row by mirroring dataclass fields onto columns.

        ``job_id`` is freshly minted (``BaseJob.job_id = ""`` is always
        empty pre-publish); ``attempts`` is claim-time bookkeeping so
        it stays at its column default. Every other dataclass field
        whose name is also a mapped column is copied via ``setattr``
        — values are taken verbatim, so a dataclass field typed as
        ``int | None`` with ``None`` will land as ``NULL`` and fail
        against a ``NOT NULL`` column with the appropriate DB error.
        Override :meth:`publish` (and skip the auto-copy by calling
        the row constructor directly) when you need defensive
        coercion or a derived column.
        """
        row = self.job_model(
            job_id=self.new_job_id(),
            status=JobStatus.PENDING,
        )
        for f in dataclasses.fields(job):
            if f.name in ("job_id", "attempts"):
                continue
            if hasattr(row, f.name):
                setattr(row, f.name, getattr(job, f.name))
        return row

    def claim(self) -> JobT | None:
        with self._session() as s:
            row = self._claim(s)
            s.commit()
            return self._map_row(row, self.job_cls) if row else None

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
            if getattr(row, "status", None) == JobStatus.PROCESSING:
                row.status = JobStatus.PENDING  # type: ignore[reportAttributeAccessIssue]
                row.leased_by = None
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
                .where(self.job_model.status == JobStatus.PENDING)  # type: ignore[reportAttributeAccessIssue]
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
        # ``leased_by`` and ``started_at`` are both guaranteed by
        # :class:`BaseJobRowMixin`, so the claim path can set them
        # unconditionally.
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
            if status == JobStatus.PROCESSING and attempts >= self.max_attempts:
                if not self._mark_exhausted(
                    session,
                    candidate,
                    now=now,
                    extra_where=extra_where,
                ):
                    session.rollback()
                continue
            is_reclaim = status == JobStatus.PROCESSING
            # Conditional UPDATE: same row, same status / lease invariants
            # — atomic on SQLite. rowcount == 1 ⇒ we own the row; 0 ⇒
            # another worker grabbed it first; retry with the next
            # candidate.
            invariant = or_(
                self.job_model.status == JobStatus.PENDING,  # type: ignore[reportAttributeAccessIssue]
                and_(
                    self.job_model.status == JobStatus.PROCESSING,  # type: ignore[reportAttributeAccessIssue]
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
                "status": JobStatus.PROCESSING,
                "leased_until": lease_until,
                "attempts": attempts + 1,
                "leased_by": owner,
            }
            if not is_reclaim:
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
                self.job_model.status == JobStatus.PENDING,  # type: ignore[reportAttributeAccessIssue]
                and_(
                    self.job_model.status == JobStatus.PROCESSING,  # type: ignore[reportAttributeAccessIssue]
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
        values: dict[str, object] = {"status": JobStatus.FAILED}
        if hasattr(self.job_model, "completed_at"):
            values["completed_at"] = now
        for field in dataclasses.fields(self.result_cls):  # type: ignore[reportArgumentType]
            if field.name != "status" and hasattr(self.job_model, field.name):
                values[field.name] = getattr(result, field.name)

        where_clauses: list[ColumnElement[bool]] = [
            self.job_model.id == candidate.id,  # type: ignore[reportAttributeAccessIssue]
            self.job_model.status == JobStatus.PROCESSING,  # type: ignore[reportAttributeAccessIssue]
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
        row.status = JobStatus.COMPLETED if result.status == JobStatus.COMPLETED else JobStatus.FAILED  # type: ignore[reportAttributeAccessIssue]
        if hasattr(row, "completed_at"):
            row.completed_at = now  # type: ignore[reportAttributeAccessIssue]
        self._write_result_to_job(row, result)

    def _get_result(self, session: Session, *, key: str) -> ResultT | None:
        row = session.scalar(
            select(self.job_model).where(getattr(self.job_model, self.natural_key_attr) == key)
        )
        if row is None or getattr(row, "status", "") not in (JobStatus.COMPLETED, JobStatus.FAILED):
            return None
        return self._read_result_from_job(row)

    # -- 键提取 ------------------------------------------------------------

    def _key_of(self, row: RowT) -> str:
        val = getattr(row, self.natural_key_attr, None)
        if val is not None:
            return str(val)
        if hasattr(row, "id"):
            return str(row.id)  # type: ignore[reportAttributeAccessIssue]
        return ""

    # -- Result 映射工具 ----------------------------------------------------

    def _write_result_to_job(self, row: RowT, result: ResultT) -> None:
        """将 result dataclass 的字段写回 ORM 行（跳过 ``status``，由
        :meth:`_submit` 显式编码到 row.status）。"""
        for f in dataclasses.fields(self.result_cls):  # type: ignore[reportArgumentType]
            if f.name == "status":
                continue
            if hasattr(row, f.name):
                setattr(row, f.name, getattr(result, f.name))

    def _read_result_from_job(self, row: RowT) -> ResultT:
        """从 ORM 行重建 result dataclass。"""
        key_val = getattr(row, self.natural_key_attr, None)
        key_val = str(key_val) if key_val is not None else ""
        kwargs: dict = {
            self.natural_key_attr: key_val,
            "status": getattr(row, "status"),
        }
        for f in dataclasses.fields(self.result_cls):  # type: ignore[reportArgumentType]
            if f.name in ("status", self.natural_key_attr):
                continue
            if hasattr(row, f.name):
                kwargs[f.name] = getattr(row, f.name)
        return self.result_cls(**kwargs)

    def _make_exhausted_result(self, row: RowT) -> ResultT:
        """构造一个"重试耗尽"的失败 Result。

        仅当 Result 子类声明了 ``error`` 字段时才写入耗尽文案——
        部分 Result（如 :class:`ChatJobResult`）用 ``error_detail``，
        不带 ``error`` 字段，硬写会抛 ``TypeError``。
        """
        key_val = getattr(row, self.natural_key_attr, None)
        key_val = str(key_val) if key_val is not None else ""
        kwargs: dict = {
            self.natural_key_attr: key_val,
            "status": JobStatus.FAILED,
        }
        field_names = {f.name for f in dataclasses.fields(self.result_cls)}  # type: ignore[reportArgumentType]
        attempts = getattr(row, "attempts", None)
        if attempts is not None and "error" in field_names:
            kwargs["error"] = f"job exhausted after {attempts} attempt(s)"
        return self.result_cls(**kwargs)
