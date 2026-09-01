"""Claimable tool-execution work for the tools Worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


@dataclass(frozen=True)
class LLMToolCall:
    """The LLM-facing portion of one requested tool execution."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class RunToolJob(BaseJob):
    """One tool invocation.

    The Worker looks up ``call.name`` and runs it with ``call.arguments``.
    Workspace path stays on the Runtime BUS; callers do not send it.
    ``call.tool_call_id`` lets the Agent match the LLM tool use; it is not
    copied onto the Result.
    """

    call: LLMToolCall


@dataclass
class RunToolResult(BaseJobResult):
    """The tool's text payload. Failures use ``status`` and ``error``."""

    content: str | None = None


class RunToolJobRow(BaseJobRow):
    __tablename__ = "jobs_run_tool"

    call: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunToolJobBoard(BaseJobBoard[RunToolJob, RunToolResult, RunToolJobRow]):
    job_cls = RunToolJob
    result_cls = RunToolResult
    row_cls = RunToolJobRow
