"""Provider configuration change notify for the provider Worker."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Text, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.engine import EngineFactory
from ...base.go import go
from ...base.hookableJobBoard import HookableJobBoard
from ..books.settingsBook import SettingRow, SettingsBook

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"


@dataclass
class ChangeProviderNotify(BaseJob):
    """Replace the Runtime's provider configuration.

    A field set to a string replaces its setting; ``None`` leaves it unchanged.
    The provider Worker verifies the candidate before the Board commits it.
    """

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None


@dataclass
class ChangeProviderNotifyResult(BaseJobResult):
    """Validation and configuration result; failures retain the old settings."""


class ChangeProviderNotifyRow(BaseJobRow):
    __tablename__ = "jobs_change_provider_notify"

    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChangeProviderNotifyBoard(
    HookableJobBoard[ChangeProviderNotify, ChangeProviderNotifyResult, ChangeProviderNotifyRow]
):
    job_cls = ChangeProviderNotify
    result_cls = ChangeProviderNotifyResult
    row_cls = ChangeProviderNotifyRow

    def __init__(self, factory: EngineFactory, *, settings: SettingsBook) -> None:
        super().__init__(factory)
        self._settings = settings

    def submit_result(self, result: ChangeProviderNotifyResult) -> bool:
        """Persist settings only after the provider has verified the candidate."""
        with self._session() as session:
            row = session.get(ChangeProviderNotifyRow, result.id)
            if row is None or row.status != JobStatus.CLAIMED.value:
                return False
            if result.status is JobStatus.COMPLETED:
                with self._settings._session() as memories:
                    for key, value in (
                        (PROVIDER_NAME_KEY, row.provider),
                        (PROVIDER_API_KEY_KEY, row.api_key),
                        (PROVIDER_MODEL_KEY, row.model),
                    ):
                        if value is None:
                            continue
                        setting = memories.scalar(select(SettingRow).where(SettingRow.key == key))
                        if setting is None:
                            memories.add(SettingRow(key=key, value=value))
                        else:
                            setting.value = value
                    memories.commit()
            self._write_result(row, result)
            session.commit()
        go(self._post_result(result))
        return True
