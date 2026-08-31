"""Provider configuration change notify for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Text, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus, NotifyJobBoard
from ...base.engine import EngineFactory
from ...base.time import utcnow
from ..books.settingsBook import SettingRow, SettingsBook

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"


@dataclass
class ChangeProviderNotify(BaseJob):
    """Replace the Runtime's provider configuration.

    A non-empty field replaces its setting; an empty field is skipped.
    Publishing persists the supplied settings atomically before the provider
    Worker claims this notify and rebuilds its in-memory client.
    """

    provider: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class ChangeProviderNotifyResult(BaseJobResult):
    """Rebuild acknowledgement. Failures use ``status`` and ``error``."""


class ChangeProviderNotifyRow(BaseJobRow):
    __tablename__ = "jobs_change_provider_notify"

    provider: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChangeProviderNotifyBoard(
    NotifyJobBoard[ChangeProviderNotify, ChangeProviderNotifyResult, ChangeProviderNotifyRow]
):
    job_cls = ChangeProviderNotify
    result_cls = ChangeProviderNotifyResult
    row_cls = ChangeProviderNotifyRow

    def __init__(self, factory: EngineFactory, *, settings: SettingsBook) -> None:
        super().__init__(factory)
        self._settings = settings

    def _publish(self, job: ChangeProviderNotify) -> int:
        """Atomically persist configuration and enqueue its rebuild signal."""
        now = utcnow()
        prepared = replace(job, created_at=now, updated_at=now)
        values = prepared.to_dict()
        values.pop("id", None)
        values["status"] = JobStatus.PENDING.value
        with self._settings._session() as books:
            for key, value in (
                (PROVIDER_NAME_KEY, job.provider),
                (PROVIDER_API_KEY_KEY, job.api_key),
                (PROVIDER_MODEL_KEY, job.model),
            ):
                if value == "":
                    continue
                setting = books.scalar(select(SettingRow).where(SettingRow.key == key))
                if setting is None:
                    books.add(SettingRow(key=key, value=value))
                else:
                    setting.value = value
            books.commit()
        with self._session() as session:
            row = ChangeProviderNotifyRow(**values)
            session.add(row)
            session.commit()
            return int(row.id)
