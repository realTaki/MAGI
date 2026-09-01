"""Provider configuration change notify for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ...base.engine import EngineFactory
from ...base.go import go
from ..books.settingsBook import Setting, SettingsBook

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"


@dataclass
class ChangeProviderNotify(BaseJob):
    """Replace the Runtime's provider configuration.

    A field set to a string replaces its setting; ``None`` leaves it unchanged.
    Publishing persists the supplied settings before the provider Worker
    claims this notify. Validation happens on CallLLMJob.
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

    def publish(self, job: ChangeProviderNotify) -> int:
        job_id = self._publish(job)
        published = replace(job, id=job_id)
        for key, value in (
            (PROVIDER_NAME_KEY, published.provider),
            (PROVIDER_API_KEY_KEY, published.api_key),
            (PROVIDER_MODEL_KEY, published.model),
        ):
            if value is None:
                continue
            self._settings.upsert(Setting(key=key, value=value))
        go(self._post_publish(published))
        return job_id
