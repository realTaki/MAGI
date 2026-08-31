"""Claimable tool-execution work for the tools Worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


@dataclass
class RunToolJob(BaseJob):
    """One tool invocation.

    The Worker looks up ``name`` and runs it with ``arguments``.
    Workspace path stays on the Runtime BUS; callers do not send it.
    ``tool_call_id`` stays on this Job so the agent can match the LLM
    tool_use; it is not copied onto the Result.
    """

    name: str
    tool_call_id: str
    arguments: dict[str, Any] | None = None


@dataclass
class RunToolResult(BaseJobResult):
    """The tool's text payload. Failures use ``status`` and ``error``."""

    content: str | None = None


class RunToolJobRow(BaseJobRow):
    __tablename__ = "jobs_run_tool"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunToolJobBoard(BaseJobBoard[RunToolJob, RunToolResult, RunToolJobRow]):
    job_cls = RunToolJob
    result_cls = RunToolResult
    row_cls = RunToolJobRow
