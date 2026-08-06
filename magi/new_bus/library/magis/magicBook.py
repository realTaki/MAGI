"""MagicBook — one row per individual MAGI agent (the ``magic`` table).

Schema mirrors the old bus's ``magic`` table.  ``provider`` /
``api_key`` / ``model`` columns are legacy/fallback; new MAGIs store
credentials in the ``settings`` table (via :class:`SettingBook`)
populated by bootstrap at startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Magic:
    id: int
    name: str | None = None
    provider: str | None = None
    api_key: str | None = None
    instruction: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _MagicRow(Base):
    __tablename__ = "magic"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True
    )
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    instruction: Mapped[str] = mapped_column(default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book ----------------------------------------------------------------


class MagicBook(BaseBook[_MagicRow, Magic]):
    model_cls = _MagicRow
    dto_cls = Magic

    def get(self, *, magic_id: int) -> Magic | None:
        with self._factory.session() as s:
            row = s.scalar(select(_MagicRow).where(_MagicRow.id == magic_id))
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> Magic | None:
        with self._factory.session() as s:
            row = s.scalar(select(_MagicRow).where(_MagicRow.name == name))
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Magic]:
        with self._factory.session() as s:
            rows = s.scalars(select(_MagicRow).order_by(_MagicRow.id)).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, name: str | None = None,
            provider: str | None = None, api_key: str | None = None,
            instruction: str = "") -> Magic:
        with self._factory.session() as s:
            row = _MagicRow(
                name=name, provider=provider, api_key=api_key,
                instruction=instruction,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update_instruction(self, *, magic_id: int, instruction: str) -> None:
        with self._factory.session() as s:
            row = s.scalar(select(_MagicRow).where(_MagicRow.id == magic_id))
            if row is None:
                return
            row.instruction = instruction
            s.commit()

    def delete(self, *, magic_id: int) -> bool:
        with self._factory.session() as s:
            row = s.scalar(select(_MagicRow).where(_MagicRow.id == magic_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["Magic", "MagicBook", "_MagicRow"]
