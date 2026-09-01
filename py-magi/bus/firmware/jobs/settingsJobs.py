"""Semantic Firmware commands for the key/value SettingsBook."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ..books.settingsBook import Setting


def _valid_key(key: str) -> bool:
    return isinstance(key, str) and bool(key.strip())


@dataclass
class GetSettingJob(BaseJob):
    key: str  


@dataclass
class GetSettingResult(BaseJobResult):
    value: str | None = None


class GetSettingJobRow(BaseJobRow):
    __tablename__ = "jobs_get_setting"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class GetSettingJobBoard(OperateBookJobBoard[GetSettingJob, GetSettingResult, GetSettingJobRow]):
    job_cls = GetSettingJob
    result_cls = GetSettingResult
    row_cls = GetSettingJobRow

    def _execute(self, job: GetSettingJob) -> GetSettingResult:
        if not _valid_key(job.key):
            return GetSettingResult(status=JobStatus.FAILED, error="setting key must be non-empty")
        setting = self._book.get(job.key)
        return GetSettingResult(value=None if setting is None else setting.value)


@dataclass
class SetSettingJob(BaseJob):
    key: str 
    value: str


@dataclass
class SetSettingResult(BaseJobResult):
    pass


class SetSettingJobRow(BaseJobRow):
    __tablename__ = "jobs_set_setting"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SetSettingJobBoard(OperateBookJobBoard[SetSettingJob, SetSettingResult, SetSettingJobRow]):
    job_cls = SetSettingJob
    result_cls = SetSettingResult
    row_cls = SetSettingJobRow

    def _execute(self, job: SetSettingJob) -> SetSettingResult:
        if not _valid_key(job.key):
            return SetSettingResult(status=JobStatus.FAILED, error="setting key must be non-empty")
        self._book.upsert(Setting(key=job.key, value=job.value))
        return SetSettingResult()


@dataclass
class DeleteSettingJob(BaseJob):
    key: str  


@dataclass
class DeleteSettingResult(BaseJobResult):
    pass


class DeleteSettingJobRow(BaseJobRow):
    __tablename__ = "jobs_delete_setting"

    key: Mapped[str] = mapped_column(Text, nullable=False)


class DeleteSettingJobBoard(
    OperateBookJobBoard[DeleteSettingJob, DeleteSettingResult, DeleteSettingJobRow]
):
    job_cls = DeleteSettingJob
    result_cls = DeleteSettingResult
    row_cls = DeleteSettingJobRow

    def _execute(self, job: DeleteSettingJob) -> DeleteSettingResult:
        if not _valid_key(job.key):
            return DeleteSettingResult(status=JobStatus.FAILED, error="setting key must be non-empty")
        setting = self._book.get(job.key)
        if setting is not None:
            self._book.delete(setting.id)
        return DeleteSettingResult()


@dataclass
class ListSettingsResult(BaseJobResult):
    settings: dict[str, str] | None = None

@dataclass
class ListSettingsJob(BaseJob[ListSettingsResult]):
    pass


class ListSettingsJobRow(BaseJobRow):
    __tablename__ = "jobs_list_settings"

    settings: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)


class ListSettingsJobBoard(
    OperateBookJobBoard[ListSettingsJob, ListSettingsResult, ListSettingsJobRow]
):
    job_cls = ListSettingsJob
    result_cls = ListSettingsResult
    row_cls = ListSettingsJobRow

    def _execute(self, job: ListSettingsJob) -> ListSettingsResult:
        del job
        settings = {item.key: item.value for item in self._book.list()}
        return ListSettingsResult(settings=settings)
