"""BUS Jobs for the internal workspace PromptsBook."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateFileBookJob import OperateFileBookJobBoard
from ..books.promptsBook import PromptsBook


@dataclass
class GetPromptJob(BaseJob):
    key: str  


@dataclass
class GetPromptResult(BaseJobResult):
    value: str | None = None


class GetPromptJobRow(BaseJobRow):
    __tablename__ = "jobs_get_prompt"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class GetPromptJobBoard(OperateFileBookJobBoard[GetPromptJob, GetPromptResult, GetPromptJobRow]):
    job_cls = GetPromptJob
    result_cls = GetPromptResult
    row_cls = GetPromptJobRow

    def __init__(self, *args, prompts: PromptsBook, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prompts = prompts

    def _execute(self, job: GetPromptJob) -> GetPromptResult:
        return GetPromptResult(value=self._prompts.get(key=job.key))


@dataclass
class SetPromptJob(BaseJob):
    key: str  
    value: str  


@dataclass
class SetPromptResult(BaseJobResult):
    pass


class SetPromptJobRow(BaseJobRow):
    __tablename__ = "jobs_set_prompt"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SetPromptJobBoard(OperateFileBookJobBoard[SetPromptJob, SetPromptResult, SetPromptJobRow]):
    job_cls = SetPromptJob
    result_cls = SetPromptResult
    row_cls = SetPromptJobRow

    def __init__(self, *args, prompts: PromptsBook, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prompts = prompts

    def _execute(self, job: SetPromptJob) -> SetPromptResult:
        if self._prompts.set(key=job.key, value=job.value):
            return SetPromptResult()
        return SetPromptResult(status=JobStatus.FAILED, error="prompt write failed")


@dataclass
class RegisterPromptJob(BaseJob):
    key: str  
    value: str  


@dataclass
class RegisterPromptResult(BaseJobResult):
    pass

class RegisterPromptJobRow(BaseJobRow):
    __tablename__ = "jobs_register_prompt"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RegisterPromptJobBoard(
    OperateFileBookJobBoard[RegisterPromptJob, RegisterPromptResult, RegisterPromptJobRow]
):
    job_cls = RegisterPromptJob
    result_cls = RegisterPromptResult
    row_cls = RegisterPromptJobRow

    def __init__(self, *args, prompts: PromptsBook, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prompts = prompts

    def _execute(self, job: RegisterPromptJob) -> RegisterPromptResult:
        return RegisterPromptResult(created=self._prompts.register(key=job.key, value=job.value))


@dataclass
class ResetPromptJob(BaseJob):
    key: str  


@dataclass
class ResetPromptResult(BaseJobResult):
    pass


class ResetPromptJobRow(BaseJobRow):
    __tablename__ = "jobs_reset_prompt"

    key: Mapped[str] = mapped_column(Text, nullable=False)


class ResetPromptJobBoard(OperateFileBookJobBoard[ResetPromptJob, ResetPromptResult, ResetPromptJobRow]):
    job_cls = ResetPromptJob
    result_cls = ResetPromptResult
    row_cls = ResetPromptJobRow

    def __init__(self, *args, prompts: PromptsBook, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prompts = prompts

    def _execute(self, job: ResetPromptJob) -> ResetPromptResult:
        if self._prompts.reset(key=job.key):
            return ResetPromptResult()
        return ResetPromptResult(status=JobStatus.FAILED, error="prompt reset failed")
