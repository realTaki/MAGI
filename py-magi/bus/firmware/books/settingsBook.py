"""SettingsBook — Firmware-owned key/value configuration records."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


@dataclass(kw_only=True)
class Setting(BaseRecord):
    """One named setting.

    Settings deliberately have no predefined vocabulary: ``key`` selects the
    setting and ``value`` holds its serialized value.  Firmware can add a new
    setting without a schema migration.
    """

    key: str
    value: str


class SettingRow(BaseRecordMixin):
    __tablename__ = "books_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_books_settings_key"),)

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SettingsBook(BaseBook[Setting]):
    record_cls = Setting
    row_cls = SettingRow

    def get(self, key: str) -> Setting | None:  # type: ignore[override]
        with self._session() as session:
            row = session.scalar(select(SettingRow).where(SettingRow.key == key))
            return None if row is None else Setting.from_row(row)

    def upsert(self, record: Setting) -> int:
        existing = self.get(record.key)
        if existing is None:
            return self.add(record)
        existing.value = record.value
        super().update(existing)
        return existing.id
