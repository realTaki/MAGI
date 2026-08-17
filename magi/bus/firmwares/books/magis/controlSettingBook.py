"""ControlSettingBook — shared MAGIS control-plane key/value state.

This is deliberately separate from ``library.local.settingBook``.  The
singleton WebUI/control process runs without a MAGI-private SQLite database,
so its authentication and onboarding state must live in the shared MAGIS
store, on both SQLite and PostgreSQL backends.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy import Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ControlSetting(BaseRecord):
    key: str  # 配置键
    value: str  # 配置值


class _ControlSettingRow(BaseRecordMixin):
    __tablename__ = "control_settings"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("key", name="uq_control_settings_key"),)


class ControlSettingBook(BaseBook[_ControlSettingRow, ControlSetting]):
    model_cls = _ControlSettingRow
    record_cls = ControlSetting

    def get_value(self, *, key: str) -> str | None:
        with self._session() as session:
            row = session.scalar(select(_ControlSettingRow).where(_ControlSettingRow.key == key))
            return row.value if row else None

    def set(self, *, key: str, value: str) -> ControlSetting:
        with self._session() as session:
            row = session.scalar(select(_ControlSettingRow).where(_ControlSettingRow.key == key))
            if row is None:
                row = _ControlSettingRow(key=key, value=value)
                session.add(row)
            else:
                row.value = value
            session.commit()
            return self._row_to_dto(row)

    def delete_by_key(self, *, key: str) -> bool:
        with self._session() as session:
            row = session.scalar(select(_ControlSettingRow).where(_ControlSettingRow.key == key))
            if row is None:
                return False
            record_id = row.id
        return self.delete(record_id)

    def list_all(self) -> list[ControlSetting]:
        with self._session() as session:
            rows = session.scalars(
                select(_ControlSettingRow).order_by(_ControlSettingRow.key)
            ).all()
            return [self._row_to_dto(row) for row in rows]


__all__ = ["ControlSetting", "ControlSettingBook"]
