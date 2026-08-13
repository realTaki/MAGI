"""callLLMJobBoard — LLM 推理作业。

Model 不传在 Job 上 —— provider worker 从缓存的配置中取当前模型。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin, JobStatus

# -- public dataclasses ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallLLMJob(BaseJob):
    """一次 LLM 推理请求。

    ``messages`` 中第一条 role="system" 的消息即为 system prompt。

    Context fields (contact / conversation / channel / caller_role / phase)
    intentionally NOT carried on this board — the provider worker only
    needs ``messages`` + ``tools`` + ``max_tokens`` + ``streaming`` to
    dial the SDK. Callers that need to thread context to the *agent*
    worker (chat / a2a / compaction / auto_title) push that context on
    a separate board (e.g. :class:`ChatJob`) which the agent worker
    reads before publishing an LLM call here.
    """

    messages: list[dict]  # LLM 消息序列；首条 role="system" 即为 system prompt
    max_tokens: int = 1024  # 单次响应上限（调用方按 provider 限制设定）
    tools: list[dict] | None = None  # 工具 schema（OpenAI-style function calling）
    streaming: bool = False  # 是否走流式（True 时 result.stream_key 非空）


@dataclass(frozen=True, slots=True)
class CallLLMResult(BaseJobResult):
    """一次 LLM 推理的完成结果。

    ``stream_key`` 非空时表示流式模式：调用方用
    ``bus.stream_hub.get(stream_key)`` 拿到 ``asyncio.Queue``，
    从中迭代读取增量文本（``None`` 哨兵表示结束）。
    """

    response: dict | None = None  # {text, thinking, tool_uses, raw_blocks} 形式的结构化结果
    finish_reason: str | None = None  # provider 返回的终止原因（stop/length/tool_use/...）
    token_usage: dict | None = None  # {prompt_tokens, completion_tokens, total_tokens}
    model: str = ""  # provider 实际使用的模型
    stream_key: str = ""  # bus.stream_hub 的管道句柄
    error: str | None = None  # 失败时的错误文案
    error_code: str = ""  # 稳定错误码，如 "LLMAuthError"


# -- internal ORM ----------------------------------------------------------


class _LLMJobRow(BaseJobRowMixin, Base):
    __tablename__ = "llm_jobs"
    __table_args__ = {"extend_existing": True}

    messages: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    tools: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    stream_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Queue -----------------------------------------------------------------


class callLLMJobBoard(BaseJobBoard[_LLMJobRow, CallLLMJob, CallLLMResult]):
    job_model = _LLMJobRow
    job_cls = CallLLMJob
    result_cls = CallLLMResult

    def publish(self, job: CallLLMJob) -> str:
        with self._session() as s:
            row = _LLMJobRow(
                job_id=uuid.uuid4().hex,
                status=JobStatus.PENDING,
                messages=job.messages,
                max_tokens=job.max_tokens,
                tools=job.tools,
                streaming=job.streaming,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
