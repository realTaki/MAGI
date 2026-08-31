"""Provider configuration change notify for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Text, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from ...base.engine import EngineFactory
from ...base.time import utcnow
from ..books.settingsBook import SettingRow, SettingsBook

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"


@dataclass
class ChangeProviderNotify(BaseJob):
    """Replace the Runtime's provider configuration.

    A field set to a string replaces its setting; ``None`` leaves it unchanged.
    Publishing persists the supplied settings atomically before the provider
    Worker claims this notify and updates its cached client in place when one
    exists. Provider, credential, and model validation occurs only while
    handling a CallLLMJob, whose terminal result carries any error back to the
    conversation.
    """

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None


@dataclass
class ChangeProviderNotifyResult(BaseJobResult):
    """Acknowledgement that the persisted configuration was observed."""


class ChangeProviderNotifyRow(BaseJobRow):
    __tablename__ = "jobs_change_provider_notify"

    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChangeProviderNotifyBoard(
    BaseJobBoard[ChangeProviderNotify, ChangeProviderNotifyResult, ChangeProviderNotifyRow]
):
    job_cls = ChangeProviderNotify
    result_cls = ChangeProviderNotifyResult
    row_cls = ChangeProviderNotifyRow

    def __init__(self, factory: EngineFactory, *, settings: SettingsBook) -> None:
        super().__init__(factory)
        self._settings = settings

    def _publish(self, job: ChangeProviderNotify) -> int:
        """Atomically persist configuration and enqueue its update signal."""
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
                if value is None:
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
