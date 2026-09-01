"""Semantic Firmware commands for the ToolsBook catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import JSON, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateBookJob import OperateBookJobBoard
from ..books.toolsBook import LLMTool, Tool, ToolsBook


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

    def _execute(self, job: GetToolJob) -> GetToolResult:
        return GetToolResult(tool=cast(ToolsBook, self._book).get_by_name(job.name.strip()))


@dataclass
class SetToolsJob(BaseJob):
    tools: list[Tool]


@dataclass
class SetToolsResult(BaseJobResult):
    pass


class SetToolsJobRow(BaseJobRow):
    __tablename__ = "jobs_set_tools"

    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class SetToolsJobBoard(OperateBookJobBoard[SetToolsJob, SetToolsResult, SetToolsJobRow]):
    job_cls = SetToolsJob
    result_cls = SetToolsResult
    row_cls = SetToolsJobRow

    def _execute(self, job: SetToolsJob) -> SetToolsResult:
        for tool in job.tools:
            name = tool.definition.name.strip()
            cast(ToolsBook, self._book).upsert(
                Tool(
                    name=name,
                    definition=LLMTool(
                        name=name,
                        description=tool.definition.description,
                        input_schema=dict(tool.definition.input_schema),
                    ),
                    enabled=tool.enabled,
                )
            )
        return SetToolsResult()


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

    def _execute(self, job: DeleteToolJob) -> DeleteToolResult:
        tool = cast(ToolsBook, self._book).get_by_name(job.name.strip())
        if tool is not None:
            self._book.delete(tool.id)
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

    def _execute(self, job: ListToolsJob) -> ListToolsResult:
        return ListToolsResult(
            tools=cast(ToolsBook, self._book).list(
                enabled=None if job.include_disabled else True
            )
        )
