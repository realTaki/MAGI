"""deliveryJobBoard — 出站投递作业。

agent 产出回复 → 入队 → worker 投递到渠道
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, _row_to_job

#: Maximum delivery attempts before a row is marked failed.
#: Distinct from :data:`BaseJobBoard.MAX_ATTEMPTS` (which gates
#: the generic per-process retry ceiling at 3) — delivery needs
#: more headroom for channel-side rate limits and reconnect
#: loops. Shared between :meth:`_mark_exhausted` and
#: :meth:`_cas_claim` (via the ``is_reclaim`` /
#: ``MAX_ATTEMPTS`` import below).
MAX_DELIVERY_ATTEMPTS = 10


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    """一次出站投递请求 — agent 产出回复后入队，对应渠道 worker claim 后送出。

    ``channel`` 决定哪个渠道 worker claim（``tg`` / ``webui`` / ...
    各自一个 :meth:`deliveryJobBoard.claim_for_channel` 调用）；
    ``payload`` 的 schema 按渠道约定（每渠道的 serialization
    helper 知道怎么转成 SDK 调用）；``destination`` 可选——
    直接渠道（如 WebUI WS 单 session）可能靠 connection 隐式
    寻址，不强制给地址。
    """

    channel: str  # 投递渠道（tg/webui/...）
    payload: dict  # 投递内容（按渠道 schema）
    destination: str | None = None  # 目标地址（chat_id 等）
    job_id: str = ""  # 自动生成的 job_id


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """:class:`DeliveryJob` 的投递回执 — 渠道 worker 实际送出后写入。

    重试预算 :data:`MAX_DELIVERY_ATTEMPTS` 用尽后由
    :meth:`BaseJobBoard._mark_exhausted` 写成 ``success=False``
    的失败 Result；正常路径下 ``success=True`` 表示 SDK 已确认
    收到 / WS 已发送完毕。``error`` 在 ``success=False`` 时填
    渠道 SDK 的错误文案或重试耗尽提示。
    """

    job_id: str  # 对应 DeliveryJob 的 job_id
    success: bool  # 投递是否成功
    error: str | None = None  # 失败时的错误描述


class _DeliveryJobRow(Base):
    __tablename__ = "delivery_outbox"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class deliveryJobBoard(BaseJobBoard[_DeliveryJobRow, DeliveryJob, DeliveryResult]):
    job_model = _DeliveryJobRow
    job_cls = DeliveryJob
    result_cls = DeliveryResult
    # Delivery needs more headroom than the generic 3 — flaky
    # channels (Telegram rate limits, WebUI WS reconnects) often
    # fail 4–5 times before settling.
    max_attempts: ClassVar[int] = MAX_DELIVERY_ATTEMPTS

    def publish(self, job: DeliveryJob) -> str:
        with self._session() as s:
            row = _DeliveryJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                channel=job.channel,
                payload=job.payload,
                destination=job.destination,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id

    def claim_for_channel(self, *, channel: str) -> DeliveryJob | None:
        """CAS-claim the oldest pending delivery row scoped to *channel*.

        Replaces the previous "claim any, release mismatches" pattern
        that caused every channel worker to thrash on rows it didn't
        own (P1 issue in the 2026-08-10 architecture review). Each
        channel worker now reads only its own row slice; no
        claim/release churn, no cross-worker race.
        """
        with self._session() as s:
            row = self._cas_claim(
                s,
                owner=f"delivery:{channel}:{id(self)}",
                extra_where=[_DeliveryJobRow.channel == channel],
            )
            s.commit()
            if row is None:
                return None
            fresh = s.get(_DeliveryJobRow, row.id)
            return _row_to_job(fresh, self.job_cls) if fresh else None
