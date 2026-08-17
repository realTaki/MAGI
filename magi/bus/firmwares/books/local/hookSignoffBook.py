"""HookSignoffBook — read-side view of pending async plugin acknowledgements.

This Book exposes the read-side view (which signoffs are pending for
which plugin on which subject), so a dispatcher (e.g. a worker) can
pick them up.

Schema for the ``hook_signoffs`` table.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from magi.bus.bases.db.base import enum_column


class HookSignoffStatus(StrEnum):
    """Async-plugin signoff lifecycle stored on ``HookSignoff.status``.

    ``PENDING`` is the row's birth state — written by the hook
    dispatcher and eligible for pickup by the polling worker.
    ``DONE`` is the terminal success state; ``FAILED`` is the
    terminal error state (the plugin acknowledged it could not
    complete). The worker treats both terminals as "stop polling".

    ``StrEnum`` rather than bare constants so typos are caught
    at lookup time instead of silently comparing False: every
    member is still a ``str`` (``HookSignoffStatus.PENDING == "pending"``),
    so existing ``where(status == "pending")`` queries and any
    raw-string comparisons keep working unchanged. Mirrors
    :class:`magi.bus.firmwares.books.local.contactBook.NoteKind`.
    """

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


# -- public dataclass ----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class HookSignoff(BaseRecord):
    subject_type: str  # 触发 hook 的对象类型
    subject_id: str  # 触发 hook 的对象 ID
    hook_point: str  # hook 触发点名称
    plugin_id: str  # 接收 signoff 的插件 ID
    status: HookSignoffStatus = HookSignoffStatus.PENDING  # 状态（pending/done/failed）
    payload: dict[str, Any] | None = None  # 附加负载
    dispatched_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _HookSignoffRow(BaseRecordMixin):
    __tablename__ = "hook_signoffs"

    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    hook_point: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[HookSignoffStatus] = mapped_column(
        enum_column(HookSignoffStatus), nullable=False, default=HookSignoffStatus.PENDING
    )
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
                .where(_HookSignoffRow.status == HookSignoffStatus.PENDING)
                .order_by(_HookSignoffRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]


__all__ = ["HookSignoff", "HookSignoffBook", "HookSignoffStatus", "_HookSignoffRow"]
