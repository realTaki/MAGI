"""TaskBook — 定时任务簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    name: str
    schedule: str
    prompt: str | None = None
    enabled: bool = True
    last_run_at: str | None = None


class _TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class TaskBook(BaseBook[_TaskRow, Task]):
    model_cls = _TaskRow
    dto_cls = Task

    def get(self, *, task_id: str) -> Task | None:
        with self._session() as s:
            row = s.scalar(
                select(_TaskRow).where(_TaskRow.task_id == task_id)
            )
            return self._row_to_dto(row) if row else None

    def list_enabled(self) -> list[Task]:
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow)
                .where(_TaskRow.enabled == True)
                .order_by(_TaskRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_all(self) -> list[Task]:
        with self._session() as s:
            rows = s.scalars(
                select(_TaskRow).order_by(_TaskRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]
