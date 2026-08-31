"""Semantic Firmware commands for the ToolsBook catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Boolean, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ..books.toolsBook import Tool, ToolRow


def _valid_name(name: str) -> bool:
    return isinstance(name, str) and bool(name.strip())


@dataclass
class GetToolJob(BaseJob):
    name: str 


@dataclass
class GetToolResult(BaseJobResult):
    tool: Tool | None = None


class GetToolJobRow(BaseJobRow):
    __tablename__ = "jobs_get_tool"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetToolJobBoard(OperateBookJobBoard[GetToolJob, GetToolResult, GetToolJobRow]):
    job_cls = GetToolJob
    result_cls = GetToolResult
    row_cls = GetToolJobRow

    def _execute(self, session: Session, job: GetToolJob) -> GetToolResult:
        if not _valid_name(job.name):
            return GetToolResult(status=JobStatus.FAILED, error="tool name must be non-empty")
        row = session.scalar(select(ToolRow).where(ToolRow.name == job.name.strip()))
        return GetToolResult(tool=None if row is None else Tool.from_row(row))


@dataclass
class SetToolJob(BaseJob):
    name: str 
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    enabled: bool = True | None = None


@dataclass
class SetToolResult(BaseJobResult):
    pass


class SetToolJobRow(BaseJobRow):
    __tablename__ = "jobs_set_tool"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SetToolJobBoard(OperateBookJobBoard[SetToolJob, SetToolResult, SetToolJobRow]):
    job_cls = SetToolJob
    result_cls = SetToolResult
    row_cls = SetToolJobRow

    def _execute(self, session: Session, job: SetToolJob) -> SetToolResult:
        if not _valid_name(job.name):
            return SetToolResult(status=JobStatus.FAILED, error="tool name must be non-empty")
        name = job.name.strip()
        row = session.scalar(select(ToolRow).where(ToolRow.name == name))
        if row is None:
            session.add(
                ToolRow(
                    name=name,
                    description=job.description,
                    input_schema=dict(job.input_schema),
                    enabled=job.enabled,
                )
            )
        else:
            row.description = job.description
            row.input_schema = dict(job.input_schema)
            row.enabled = job.enabled
        session.flush()
        return SetToolResult()


@dataclass
class DeleteToolJob(BaseJob):
    name: str


@dataclass
class DeleteToolResult(BaseJobResult):
    pass


class DeleteToolJobRow(BaseJobRow):
    __tablename__ = "jobs_delete_tool"

    name: Mapped[str] = mapped_column(Text, nullable=False)


class DeleteToolJobBoard(OperateBookJobBoard[DeleteToolJob, DeleteToolResult, DeleteToolJobRow]):
    job_cls = DeleteToolJob
    result_cls = DeleteToolResult
    row_cls = DeleteToolJobRow

    def _execute(self, session: Session, job: DeleteToolJob) -> DeleteToolResult:
        if not _valid_name(job.name):
            return DeleteToolResult(status=JobStatus.FAILED, error="tool name must be non-empty")
        row = session.scalar(select(ToolRow).where(ToolRow.name == job.name.strip()))
        if row is None:
            return DeleteToolResult()
        session.delete(row)
        return DeleteToolResult()


@dataclass
class ListToolsResult(BaseJobResult):
    tools: list[Tool] | None = None


@dataclass
class ListToolsJob(BaseJob[ListToolsResult]):
    include_disabled: bool = False


class ListToolsJobRow(BaseJobRow):
    __tablename__ = "jobs_list_tools"

    include_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tools: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListToolsJobBoard(OperateBookJobBoard[ListToolsJob, ListToolsResult, ListToolsJobRow]):
    job_cls = ListToolsJob
    result_cls = ListToolsResult
    row_cls = ListToolsJobRow

    def _execute(self, session: Session, job: ListToolsJob) -> ListToolsResult:
        stmt = select(ToolRow).order_by(ToolRow.name)
        if not job.include_disabled:
            stmt = stmt.where(ToolRow.enabled.is_(True))
        return ListToolsResult(tools=[Tool.from_row(row) for row in session.scalars(stmt)])
