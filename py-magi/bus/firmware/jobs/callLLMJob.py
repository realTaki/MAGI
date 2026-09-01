"""Claimable, backend-neutral LLM inference work for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ..books.toolsBook import LLMTool
from .runToolJob import LLMToolCall


class LLMMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMFinishReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_OUTPUT = "max_output"
    REFUSED = "refused"


@dataclass(frozen=True)
class LLMMessage:
    """One backend-neutral item in an LLM conversation."""

    role: LLMMessageRole
    text: str
    tool_calls: list[LLMToolCall] | None = None
    tool_call_id: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class LLMUsage:
    """Provider-independent token accounting, when it is reported."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass
class CallLLMJob(BaseJob):
    """One backend-neutral text-and-tools completion request.

    Provider selection, credentials, endpoint and SDK-specific options remain
    private Settings/adapter concerns. Streaming remains absent until BUS has a
    durable stream contract.
    """

    messages: list[LLMMessage]
    tools: list[LLMTool]
    max_output_tokens: int = 1024


@dataclass
class CallLLMResult(BaseJobResult):
    """The terminal response, ready for the Agent to append to its history."""

    message: LLMMessage | None = None
    finish_reason: LLMFinishReason | None = None
    usage: LLMUsage | None = None
    model: str | None = None


class CallLLMJobRow(BaseJobRow):
    __tablename__ = "jobs_call_llm"

    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    message: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)


class CallLLMJobBoard(BaseJobBoard[CallLLMJob, CallLLMResult, CallLLMJobRow]):
    job_cls = CallLLMJob
    result_cls = CallLLMResult
    row_cls = CallLLMJobRow
