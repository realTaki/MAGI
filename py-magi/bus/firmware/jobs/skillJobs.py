"""BUS Jobs for the internal workspace SkillsBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateFileBookJob import OperateFileBookJobBoard
from ..books.skillsBook import Skill, SkillsBook


@dataclass
class GetSkillJob(BaseJob):
    name: str


@dataclass
class GetSkillResult(BaseJobResult):
    content: str | None = None


class GetSkillJobRow(BaseJobRow):
    __tablename__ = "jobs_get_skill"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


class GetSkillJobBoard(OperateFileBookJobBoard[GetSkillJob, GetSkillResult, GetSkillJobRow]):
    job_cls = GetSkillJob
    result_cls = GetSkillResult
    row_cls = GetSkillJobRow

    def __init__(self, *args, skills: SkillsBook, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._skills = skills

    def _execute(self, job: GetSkillJob) -> GetSkillResult:
        return GetSkillResult(content=self._skills.read(job.name))


@dataclass
class ListSkillsResult(BaseJobResult):
    skills: list[Skill] | None = None


@dataclass
class ListSkillsJob(BaseJob[ListSkillsResult]):
    pass


class ListSkillsJobRow(BaseJobRow):
    __tablename__ = "jobs_list_skills"

    skills: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListSkillsJobBoard(OperateFileBookJobBoard[ListSkillsJob, ListSkillsResult, ListSkillsJobRow]):
    job_cls = ListSkillsJob
    result_cls = ListSkillsResult
    row_cls = ListSkillsJobRow

    def __init__(self, *args, skills: SkillsBook, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._skills = skills

    def _execute(self, job: ListSkillsJob) -> ListSkillsResult:
        del job
        return ListSkillsResult(skills=self._skills.list())
