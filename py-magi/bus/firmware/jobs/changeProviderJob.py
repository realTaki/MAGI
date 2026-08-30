"""Provider configuration change work for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Text, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from ...base.time import utcnow
from ..books.settingsBook import SettingRow

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"


@dataclass
class ChangeProviderJob(BaseJob):
    """Replace the Runtime's provider configuration.

    A non-empty field replaces its setting; an empty field is skipped.
    Publishing persists the supplied settings atomically before the provider
    Worker claims this job and rebuilds its in-memory client.
    """

    provider: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class ChangeProviderResult(BaseJobResult):
    pass


class ChangeProviderJobRow(BaseJobRow):
    __tablename__ = "jobs_change_provider"

    provider: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChangeProviderJobBoard(
    BaseJobBoard[ChangeProviderJob, ChangeProviderResult, ChangeProviderJobRow]
):
    job_cls = ChangeProviderJob
    result_cls = ChangeProviderResult
    row_cls = ChangeProviderJobRow

    def _publish(self, job: ChangeProviderJob) -> int:
        """Atomically persist configuration and enqueue its rebuild signal."""
        now = utcnow()
        prepared = replace(job, created_at=now, updated_at=now)
        values = prepared.to_dict()
        values.pop("id", None)
        values["status"] = JobStatus.PENDING.value
        with self._session() as session:
            for key, value in (
                (PROVIDER_NAME_KEY, job.provider),
                (PROVIDER_API_KEY_KEY, job.api_key),
                (PROVIDER_MODEL_KEY, job.model),
            ):
                if value == "":
                    continue
                setting = session.scalar(select(SettingRow).where(SettingRow.key == key))
                if setting is None:
                    session.add(SettingRow(key=key, value=value))
                else:
                    setting.value = value
            row = ChangeProviderJobRow(**values)
            session.add(row)
            session.commit()
            return int(row.id)
