"""callLLMJobBoard — LLM 推理作业。

Model 不传在 Job 上 —— provider worker 从缓存的配置中取当前模型。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard


# -- public dataclasses ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class CallLLMJob:
    """一次 LLM 推理请求。

    ``messages`` 中第一条 role="system" 的消息即为 system prompt。
    ``parameters`` 为调用方透传的 opaque 数据（如 uid/session_id 等上下文）。
    """

    messages: list[dict]
    max_tokens: int = 1024
    tools: list[dict] | None = None
    streaming: bool = False
    parameters: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class CallLLMResult:
    """一次 LLM 推理的完成结果。"""

    job_id: str
    success: bool
    response: dict | None = None       # {text, thinking, tool_uses, raw_blocks}
    finish_reason: str | None = None
    token_usage: dict | None = None
    model: str = ""                     # provider 实际使用的模型
    error: str | None = None
    error_code: str = ""                # 稳定错误码，如 "LLMAuthError"


# -- internal ORM ----------------------------------------------------------

class _LLMJobRow(Base):
    __tablename__ = "llm_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    messages: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    tools: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Queue -----------------------------------------------------------------

class callLLMJobBoard(BaseJobBoard[_LLMJobRow, CallLLMJob, CallLLMResult]):
    job_model = _LLMJobRow
    job_cls = CallLLMJob
    result_cls = CallLLMResult

    def publish(self, job: CallLLMJob) -> str:
        with self._factory.session() as s:
            row = _LLMJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                messages=job.messages,
                max_tokens=job.max_tokens,
                tools=job.tools,
                streaming=job.streaming,
                parameters=job.parameters,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
