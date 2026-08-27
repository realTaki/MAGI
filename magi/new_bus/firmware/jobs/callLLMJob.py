"""Claimable LLM inference work for the provider Worker."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


class LLMErrorCode(StrEnum):
    """Stable error categories exposed to LLM callers."""

    CREDENTIALS_REQUIRED = "llm.credentials_required"
    AUTH_FAILED = "llm.auth_failed"
    RATE_LIMITED = "llm.rate_limited"
    NETWORK_ERROR = "llm.network_error"
    CONTEXT_TOO_LONG = "llm.context_too_long"
    PROVIDER_CRASHED = "llm.provider_crashed"
    RUN_CANCELLED = "llm.run_cancelled"
    UNKNOWN = "llm.unknown"


@dataclass
class CallLLMJob(BaseJob):
    """One vendor-neutral LLM inference request.

    The provider selection and credentials stay in SettingsBook; the caller
    supplies only the prompt payload and invocation limits.  Streaming is
    deliberately absent until vNext has a durable stream contract.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    contact_id: int | None = None
    max_tokens: int = 1024
    tools: list[dict[str, Any]] | None = None


@dataclass
class CallLLMResult(BaseJobResult):
    """The terminal LLM response, or a stable failure category."""

    text: str = ""
    thinking: str | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    raw_blocks: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    model: str = ""
    error_code: LLMErrorCode | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        result = super().parse(data)
        if result.error_code is not None and not isinstance(result.error_code, LLMErrorCode):
            result.error_code = LLMErrorCode(result.error_code)
        return result


class CallLLMJobRow(BaseJobRow):
    __tablename__ = "jobs_call_llm"

    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    tools: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_uses: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    raw_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class CallLLMJobBoard(BaseJobBoard[CallLLMJob, CallLLMResult, CallLLMJobRow]):
    job_cls = CallLLMJob
    result_cls = CallLLMResult
    row_cls = CallLLMJobRow
