"""Claimable tool-execution work for the tools Worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


@dataclass
class RunToolJob(BaseJob):
    """One tool invocation.

    The Worker looks up ``tool_name`` and runs it with ``arguments``.
    Workspace path stays on the Runtime BUS; callers do not send it.
    ``tool_call_id`` stays on this Job so the agent can match the LLM
    tool_use; it is not copied onto the Result.
    """

    tool_name: str  
    tool_call_id: str  
    conversation_id: int 
    arguments: dict[str, Any] | None = None
    


@dataclass
class RunToolResult(BaseJobResult):
    """The tool's text payload. Failures use ``status`` and ``error``."""

    content: str | None = None


class RunToolJobRow(BaseJobRow):
    __tablename__ = "jobs_run_tool"

    tool_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RunToolJobBoard(BaseJobBoard[RunToolJob, RunToolResult, RunToolJobRow]):
    job_cls = RunToolJob
    result_cls = RunToolResult
    row_cls = RunToolJobRow
