"""ControlSettingBook — shared MAGIS control-plane key/value state.

This is deliberately separate from ``library.local.settingBook``.  The
singleton WebUI/control process runs without a MAGI-private SQLite database,
so its authentication and onboarding state must live in the shared MAGIS
store, on both SQLite and PostgreSQL backends.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base
from magi.bus.library.base import BaseBook


@dataclass(frozen=True, slots=True)
class ControlSetting:
    key: str
    value: str


class _ControlSettingRow(Base):
    __tablename__ = "control_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class ControlSettingBook(BaseBook[_ControlSettingRow, ControlSetting]):
    model_cls = _ControlSettingRow
    dto_cls = ControlSetting

    def get(self, *, key: str) -> str | None:
        with self._session() as session:
            row = session.get(_ControlSettingRow, key)
            return row.value if row else None

    def set(self, *, key: str, value: str) -> ControlSetting:
        with self._session() as session:
            row = session.get(_ControlSettingRow, key)
            if row is None:
                row = _ControlSettingRow(key=key, value=value)
                session.add(row)
            else:
                row.value = value
            session.commit()
            return self._row_to_dto(row)

    def delete(self, *, key: str) -> bool:
        with self._session() as session:
            row = session.get(_ControlSettingRow, key)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list_all(self) -> list[ControlSetting]:
        with self._session() as session:
            rows = session.scalars(select(_ControlSettingRow).order_by(_ControlSettingRow.key)).all()
            return [self._row_to_dto(row) for row in rows]


__all__ = ["ControlSetting", "ControlSettingBook"]
