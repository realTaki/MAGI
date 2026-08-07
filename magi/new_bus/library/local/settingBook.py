"""SettingBook — local SQLite KV (system.timezone, tool_max_iterations, etc.).

Each row is a (key, value) string pair. Used for runtime-configurable
system settings. The schema mirrors the old bus's
``magi.bus.db.models.local.setting.Setting`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _SettingRow(Base):
    __tablename__ = "settings"
    # ``setSettingNotify`` (in ``magi.new_bus.guild``) registers the
    # same Table for its fire-and-forget path; whichever module is
    # imported first wins, and the other must opt-in.
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book -----------------------------------------------------------------


class SettingBook(BaseBook[_SettingRow, Setting]):
    """Key/value store backed by the ``settings`` table.

    Provides basic CRUD over arbitrary keys.  Callers are responsible
    for the key vocabulary (``system.timezone``, etc.); this book
    does not enforce any schema.
    """

    model_cls = _SettingRow
    dto_cls = Setting

    def get(self, *, key: str) -> str | None:
        with self._factory.session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            return row.value if row else None

    def set(self, *, key: str, value: str) -> Setting:
        with self._factory.session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            if row is None:
                row = _SettingRow(key=key, value=value)
                s.add(row)
            else:
                row.value = value
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def delete(self, *, key: str) -> bool:
        with self._factory.session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def list_keys(self) -> list[str]:
        with self._factory.session() as s:
            rows = s.scalars(select(_SettingRow.key)).all()
            return list(rows)

    def list_all(self) -> list[Setting]:
        with self._factory.session() as s:
            rows = s.scalars(select(_SettingRow).order_by(_SettingRow.key)).all()
            return [self._row_to_dto(r) for r in rows]


__all__ = ["Setting", "SettingBook", "_SettingRow"]
