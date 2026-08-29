"""callLLMJobBoard — LLM 推理作业。

Model 不传在 Job 上 —— provider worker 从缓存的配置中取当前模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.db.base import enum_column
from old_bus.bases.job import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin

# -- public enum -----------------------------------------------------------


class LLMErrorCode(StrEnum):
    """Stable error code returned on a failed :class:`CallLLMResult`.

    Three layers of values:

    - ``NONE`` — sentinel for "result is successful; the worker never
      set an error". Stored as ``""`` so the existing
      ``if result.error_code:`` truthy check still works.
    - ``CREDENTIALS_REQUIRED`` / ``PROVIDER_CRASHED`` / ``RUN_CANCELLED``
      — bus-wide codes already in use by :class:`bus.bases.job.JobStatus`
      neighbours; keeping them verbatim avoids cross-board wire churn.
    - ``AUTH_FAILED`` / ``RATE_LIMITED`` / ``NETWORK_ERROR`` /
      ``CONTEXT_TOO_LONG`` — one member per known provider exception
      class under :mod:`providers.errors`; the provider worker
      maps ``isinstance(exc, ...)`` to the matching member instead of
      leaking ``type(exc).__name__`` into the wire format.
    - ``UNKNOWN`` — fallback for a brand-new exception class not yet
      enumerated here. The provider worker keeps the original class
      name in :attr:`CallLLMResult.error` for diagnostics.
    """

    NONE = ""
    CREDENTIALS_REQUIRED = "magi.llm_credentials_required"
    AUTH_FAILED = "llm.auth_failed"
    RATE_LIMITED = "llm.rate_limited"
    NETWORK_ERROR = "llm.network_error"
    CONTEXT_TOO_LONG = "llm.context_too_long"
    PROVIDER_CRASHED = "chat.provider_crashed"
    RUN_CANCELLED = "magi.run_cancelled"
    UNKNOWN = "llm.unknown"

# -- public dataclasses ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallLLMJob(BaseJob):
    """一次 LLM 推理请求。

    ``messages`` 中第一条 role="system" 的消息即为 system prompt。

    ``contact_id`` 是唯一携带的业务上下文——provider worker 在每次
    成功调用后写 ``token_usage`` 记账行，需要知道这笔 token 算在哪个
    联系人头上（``None`` 表示无归属，如 compaction 的独立调用）。
    其余上下文（conversation / channel / caller_role / phase）仍不
    携带：provider worker 只需 ``messages`` + ``tools`` +
    ``max_tokens`` + ``streaming`` 即可拨号，调用方需要区分来源时在
    自己的层做。
    """

    messages: list[dict]  # LLM 消息序列；首条 role="system" 即为 system prompt
    contact_id: int | None = None  # 记账归属联系人（provider 写 token_usage 用）
    max_tokens: int = 1024  # 单次响应上限（调用方按 provider 限制设定）
    tools: list[dict] | None = None  # 工具 schema（OpenAI-style function calling）
    streaming: bool = False  # 是否走流式（True 时 result.stream_key 非空）


@dataclass(frozen=True, slots=True)
class CallLLMResult(BaseJobResult):
    """一次 LLM 推理的完成结果。

    ``stream_key`` 非空时表示流式模式：调用方用
    ``bus.stream_hub.get(stream_key)`` 拿到 ``asyncio.Queue``，
    从中迭代读取增量文本（``None`` 哨兵表示结束）。

    token 用量不在此回传——provider worker 在成功调用后直接写
    ``token_usage`` 表，调用方无需关心。
    """

    response: dict | None = None  # {text, thinking, tool_uses, raw_blocks} 形式的结构化结果
    finish_reason: str | None = None  # provider 返回的终止原因（stop/length/tool_use/...）
    model: str = ""  # provider 实际使用的模型
    stream_key: str = ""  # bus.stream_hub 的管道句柄
    error_code: LLMErrorCode = LLMErrorCode.NONE  # 稳定错误码（成功 = LLMErrorCode.NONE）


# -- internal ORM ----------------------------------------------------------


class _LLMJobRow(BaseJobRowMixin):
    __tablename__ = "llm_jobs"
    __table_args__ = {"extend_existing": True}

    messages: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    tools: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stream_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[LLMErrorCode] = mapped_column(
        enum_column(LLMErrorCode), nullable=False, default=LLMErrorCode.NONE
    )


# -- Queue -----------------------------------------------------------------


class callLLMJobBoard(BaseJobBoard[_LLMJobRow, CallLLMJob, CallLLMResult]):
    job_model = _LLMJobRow
    job_cls = CallLLMJob
    result_cls = CallLLMResult
