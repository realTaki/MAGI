"""runToolJobBoard — 工具执行作业。

worker claim → 执行工具 → submit_result

Result shape mirrors :class:`CallLLMResult`: ``success`` is
the gate, ``error`` / ``error_code`` are the failure pair
(human-readable + machine-stable), ``content`` / ``is_error``
are the legacy :class:`ToolResult` fields the executable
``Tool.run()`` returns. ``run_id`` / ``tool_call_id`` round-
trip with :class:`RunToolJob` so the agent can correlate a
finished tool call with the LLM turn that produced it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard


@dataclass(frozen=True, slots=True)
class RunToolJob:
    """一个工具执行 job。

    字段集对齐 ``ToolClaim``——``attempts`` /
    ``source`` / ``catalog_revision`` / ``schema_hash`` 都是
    worker claim 时需要的校验元数据。

    ``catalog_revision`` 是 agent publish job 那一刻 catalog
    的 revision；worker claim 后会查当前 catalog revision，如
    果 > claim 上的 revision 就拒绝（catalog 在 agent 调度期
    间被替换过，agent 用的 schema 过期）。

    ``schema_hash`` 是 agent 调用 tool 时那个 tool 的 schema
    哈希；worker claim 后会跟当前 tool 的 schema_hash 比较，
    不一致就拒绝。
    """

    tool_name: str
    payload: dict
    run_id: str = ""
    tool_call_id: str = ""
    job_id: str = ""
    attempts: int = 0
    source: str | None = None
    catalog_revision: int | None = None
    schema_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RunToolResult:
    """工具执行的完成结果。

    ``content`` + ``is_error`` 是 :class:`ToolResult` 的直通
    字段；``success`` 是给 :class:`BaseJobBoard._submit` 用的
    闸门；``error`` / ``error_code`` 是错误文案 + 稳定错误码
    对（与 :class:`CallLLMResult` 对齐，方便上层做 retry 决
    策）。
    """

    job_id: str
    success: bool
    content: str = ""
    is_error: bool = False
    error: str | None = None
    error_code: str = ""
    run_id: str = ""
    tool_call_id: str = ""
    # 给上层 structured use（与 ``content`` 不冲突；
    # ``content`` 是 ``str``，``result`` 是 ``dict``）。
    result: dict | None = None


class _ToolJobRow(Base):
    __tablename__ = "tool_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), default="")
    tool_call_id: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    catalog_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # -- result-side columns (aligned with RunToolResult) -----------------
    # ``content`` stores the plain-text ToolResult.content
    # (truncated to 8 KB by the worker); ``result`` stores the
    # structured payload for callers that want it. Both can be
    # populated independently.
    content: Mapped[str] = mapped_column(String(8192), nullable=False, default="")
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # -- lease / retry bookkeeping ---------------------------------------
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # -- timestamps ------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class runToolJobBoard(BaseJobBoard[_ToolJobRow, RunToolJob, RunToolResult]):
    job_model = _ToolJobRow
    job_cls = RunToolJob
    result_cls = RunToolResult

    def publish(self, job: RunToolJob) -> str:
        with self._session() as s:
            row = _ToolJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                tool_name=job.tool_name,
                payload=job.payload,
                run_id=job.run_id,
                tool_call_id=job.tool_call_id,
                source=job.source or "",
                catalog_revision=int(job.catalog_revision or 0),
                schema_hash=job.schema_hash or "",
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
