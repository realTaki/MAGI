"""Claimable LLM inference work for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


@dataclass
class CallLLMJob(BaseJob):
    """One vendor-neutral LLM inference request.

    The provider selection and credentials stay in SettingsBook; the caller
    supplies only the prompt payload and invocation limits.  Streaming is
    deliberately absent until vNext has a durable stream contract.
    """
    messages: list[dict[str, Any]] 
    contact_id: int 
    tools: list[dict[str, Any]] 
    max_tokens: int = 1024
    


@dataclass
class CallLLMResult(BaseJobResult):
    """The terminal LLM response."""

    text: str = ""
    thinking: str | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    raw_blocks: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    model: str | None = None


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


class CallLLMJobBoard(BaseJobBoard[CallLLMJob, CallLLMResult, CallLLMJobRow]):
    job_cls = CallLLMJob
    result_cls = CallLLMResult
    row_cls = CallLLMJobRow
