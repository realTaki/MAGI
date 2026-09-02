"""Claimable, backend-neutral LLM inference work for the provider Worker."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.hookableJobBoard import HookableJobBoard
from ..books.toolsBook import LLMTool
from .runToolJob import LLMToolCall


class LLMMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class LLMMessage:
    """One backend-neutral item in an LLM conversation.

    ``content`` is the user-visible text. ``thinking_blocks`` is provider
    thinking to resend on the next tool turn; it is not delivered to chat.
    """

    role: LLMMessageRole
    content: str
    tool_calls: list[LLMToolCall] | None = None
    tool_call_id: str | None = None
    is_error: bool = False
    thinking_blocks: list[dict[str, Any]] | None = None

    def estimated_tokens(self) -> int:
        """Cheap provider-neutral estimate of this message's context cost."""
        total = 4 + len(self.content) // 4

        def default(item: Any):
            return asdict(item) if is_dataclass(item) else str(item)

        for value in (self.tool_calls, self.thinking_blocks):
            if value:
                total += len(json.dumps(value, ensure_ascii=False, default=default)) // 4
        return total

@dataclass
class CallLLMJob(BaseJob):
    """One backend-neutral text-and-tools completion request.

    ``max_tokens`` caps the completion. Provider selection, credentials and
    SDK-specific options remain private Settings/adapter concerns. Streaming
    remains absent until BUS has a durable stream contract.
    """

    messages: list[LLMMessage]
    tools: list[LLMTool]
    max_tokens: int = 1024


@dataclass
class CallLLMResult(BaseJobResult):
    """The terminal response, ready for the Agent to append to its history."""

    message: LLMMessage | None = None


class CallLLMJobRow(BaseJobRow):
    __tablename__ = "jobs_call_llm"

    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    message: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class CallLLMJobBoard(HookableJobBoard[CallLLMJob, CallLLMResult, CallLLMJobRow]):
    job_cls = CallLLMJob
    result_cls = CallLLMResult
    row_cls = CallLLMJobRow
