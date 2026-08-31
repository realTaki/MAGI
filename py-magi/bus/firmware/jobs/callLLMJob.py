"""Claimable, backend-neutral LLM inference work for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ..books.toolsBook import Tool
from .runToolJob import RunToolJob


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
    """One backend-neutral item in an LLM conversation.

    ``system`` and ``user`` carry text. ``assistant`` carries text and/or
    tool calls. ``tool`` carries one tool result matched by ``tool_call_id``.
    """

    role: LLMMessageRole
    text: str
    tool_calls: list[RunToolJob] | None = None
    tool_call_id: str | None = None
    is_error: bool = False

    def __post_init__(self) -> None:
        role = LLMMessageRole(self.role)
        object.__setattr__(self, "role", role)
        if not isinstance(self.text, str):
            raise TypeError("LLM message text must be a string")
        if not all(isinstance(call, RunToolJob) for call in self.tool_calls or ()):
            raise TypeError("LLM message tool_calls must contain RunToolJob values")
        if role is LLMMessageRole.TOOL:
            if not self.tool_call_id:
                raise ValueError("tool messages require tool_call_id")
            if self.tool_calls:
                raise ValueError("tool messages cannot contain tool_calls")
            return
        if self.tool_call_id is not None or self.is_error:
            raise ValueError("only tool messages may set tool_call_id or is_error")
        if role is not LLMMessageRole.ASSISTANT and self.tool_calls:
            raise ValueError("only assistant messages may contain tool_calls")


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
    tools: list[Tool]
    max_output_tokens: int = 1024

    def __post_init__(self) -> None:
        if not all(isinstance(message, LLMMessage) for message in self.messages):
            raise TypeError("CallLLMJob.messages must contain LLMMessage values")
        if not all(isinstance(tool, Tool) for tool in self.tools):
            raise TypeError("CallLLMJob.tools must contain Tool values")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


@dataclass
class CallLLMResult(BaseJobResult):
    """The terminal response, ready for the Agent to append to its history."""

    message: LLMMessage | None = None
    finish_reason: LLMFinishReason | None = None
    usage: LLMUsage | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.message is not None and not isinstance(self.message, LLMMessage):
            raise TypeError("CallLLMResult.message must be an LLMMessage")
        if self.usage is not None and not isinstance(self.usage, LLMUsage):
            raise TypeError("CallLLMResult.usage must be an LLMUsage")


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
