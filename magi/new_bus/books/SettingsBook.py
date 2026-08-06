"""SettingsBook — 设置簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str | None = None


class _SettingRow(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class SettingsBook(BaseBook[_SettingRow, Setting]):
    model_cls = _SettingRow
    dto_cls = Setting

    def get(self, *, key: str) -> Setting | None:
        with self._session() as s:
            row = s.scalar(
                select(_SettingRow).where(_SettingRow.key == key)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Setting]:
        with self._session() as s:
            rows = s.scalars(
                select(_SettingRow).order_by(_SettingRow.key)
            ).all()
            return [self._row_to_dto(r) for r in rows]
