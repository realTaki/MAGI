"""HookSignoffBook — read-side view of pending async plugin acknowledgements.

This Book exposes the read-side view (which signoffs are pending for
which plugin on which subject), so a dispatcher (e.g. a worker) can
pick them up.

Schema for the ``hook_signoffs`` table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import Strict
from sqlalchemy import (
    JSON,
    DateTime,
    String,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin, record

# -- public dataclass ----------------------------------------------------


@record
class HookSignoff(BaseRecord):
    subject_type: str  # 触发 hook 的对象类型
    subject_id: str  # 触发 hook 的对象 ID
    hook_point: str  # hook 触发点名称
    plugin_id: str  # 接收 signoff 的插件 ID
    status: str = "pending"  # 状态（pending/done/failed）
    payload: dict[str, Any] | None = None  # 附加负载
    dispatched_at: Annotated[datetime, Strict()] | None = None


# -- internal ORM --------------------------------------------------------


class _HookSignoffRow(BaseRecordMixin):
    __tablename__ = "hook_signoffs"

    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hook_point: Mapped[str] = mapped_column(String(64), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# -- Book ----------------------------------------------------------------


class HookSignoffBook(BaseBook[_HookSignoffRow, HookSignoff]):
    """Read-side Book for the ``hook_signoffs`` table.

    Callers can read pending signoffs here; they should not write
    or delete rows directly.
    """

    model_cls = _HookSignoffRow
    record_cls = HookSignoff

    def list_pending(self) -> list[HookSignoff]:
        with self._session() as s:
            rows = s.scalars(
                select(_HookSignoffRow)
                .where(_HookSignoffRow.status == "pending")
                .order_by(_HookSignoffRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]


__all__ = ["HookSignoff", "HookSignoffBook", "_HookSignoffRow"]
