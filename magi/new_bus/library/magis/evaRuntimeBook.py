"""EvaRuntimeBook — desired/observed state for EVA Kubernetes Deployments.

Schema mirrors the old bus's ``eva_runtimes`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvaRuntime:
    id: int
    magic_id: int
    deployment_name: str
    desired_state: str
    observed_state: str = "unknown"
    namespace: str | None = None
    image: str | None = None
    extra: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _EvaRuntimeRow(Base):
    __tablename__ = "eva_runtimes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    magic_id: Mapped[int] = mapped_column(
        ForeignKey("magic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deployment_name: Mapped[str] = mapped_column(String(120), nullable=False)
    desired_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Started"
    )
    observed_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Unknown"
    )
    namespace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book ----------------------------------------------------------------


class EvaRuntimeBook(BaseBook[_EvaRuntimeRow, EvaRuntime]):
    model_cls = _EvaRuntimeRow
    dto_cls = EvaRuntime

    def get(self, *, runtime_id: int) -> EvaRuntime | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_EvaRuntimeRow).where(_EvaRuntimeRow.id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def get_for_magic(self, *, magic_id: int) -> EvaRuntime | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_EvaRuntimeRow)
                .where(_EvaRuntimeRow.magic_id == magic_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[EvaRuntime]:
        with self._factory.session() as s:
            rows = s.scalars(select(_EvaRuntimeRow).order_by(_EvaRuntimeRow.id)).all()
            return [self._row_to_dto(r) for r in rows]

    def upsert(self, *, magic_id: int, deployment_name: str,
               desired_state: str = "Started",
               observed_state: str = "Unknown",
               namespace: str | None = None,
               image: str | None = None,
               extra: str | None = None) -> EvaRuntime:
        with self._factory.session() as s:
            row = s.scalar(
                select(_EvaRuntimeRow)
                .where(_EvaRuntimeRow.magic_id == magic_id)
            )
            if row is None:
                row = _EvaRuntimeRow(
                    magic_id=magic_id, deployment_name=deployment_name,
                    desired_state=desired_state, observed_state=observed_state,
                    namespace=namespace, image=image, extra=extra,
                )
                s.add(row)
            else:
                row.deployment_name = deployment_name
                row.desired_state = desired_state
                row.observed_state = observed_state
                row.namespace = namespace
                row.image = image
                row.extra = extra
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def set_observed(self, *, runtime_id: int, observed_state: str) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_EvaRuntimeRow).where(_EvaRuntimeRow.id == runtime_id)
            )
            if row is None:
                return
            row.observed_state = observed_state
            s.commit()


__all__ = ["EvaRuntime", "EvaRuntimeBook", "_EvaRuntimeRow"]
