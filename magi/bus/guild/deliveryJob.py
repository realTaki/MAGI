"""deliveryJobBoard — 出站投递作业。

agent 产出回复 → 入队 → worker 投递到渠道
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, _row_to_job

#: Maximum delivery attempts before a row is marked failed.
#: Distinct from :data:`magi.bus.guild.base.MAX_ATTEMPTS` (which
#: gates the generic per-process retry ceiling at 3) — delivery
#: needs more headroom for channel-side rate limits and reconnect
#: loops. Read by :meth:`BaseJobBoard._mark_exhausted` and the
#: ``is_reclaim`` branch of :meth:`BaseJobBoard._cas_claim` via
#: ``BaseJobBoard.max_attempts``, which this board overrides on
#: the next line.
MAX_DELIVERY_ATTEMPTS = 10


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    """一次出站投递请求 — agent 产出回复后入队，对应渠道 worker claim 后送出。

    ``channel`` 决定哪个渠道 worker claim（``tg`` / ``webui`` / ...
    各自一个 :meth:`deliveryJobBoard.claim_for_channel` 调用）；
    ``destination`` 可选——直接渠道（如 WebUI WS 单 session）
    可能靠 connection 隐式寻址，不强制给地址。

    投递内容（``text`` / ``conversation_id`` / ``contact_id``）以
    **类型化字段** 暴露,producer / consumer 看到的是具名属性。
    在 DB 层每个字段都是独立列（见 :class:`_DeliveryJobRow`）,
    没有 ``payload`` JSON 黑盒——ORM 行 → dataclass 直接按字段
    名映射,migration 0017 已经把旧的 ``payload`` 列拆掉。
    """

    channel: str  # 投递渠道（tg/webui/...）
    text: str = ""  # 投递文本（最终回复 / send_message 文本）
    conversation_id: str | None = None  # 关联会话（webui worker 用）
    contact_id: int | None = None  # 关联 contact（webui worker 用）
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
    # Delivery content — formerly a single ``payload`` JSON blob. Split
    # into individual columns in migration 0017 so producers / consumers
    # see one field per attribute on :class:`DeliveryJob` (no
    # ``payload`` dict). The pre-migration rows had no value here.
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
                text=job.text,
                conversation_id=job.conversation_id,
                contact_id=job.contact_id,
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

    def claim(self) -> DeliveryJob | None:
        """Generic (no channel scope) claim — falls through to the base.

        No bridge needed: every :class:`DeliveryJob` field is its own
        column on :class:`_DeliveryJobRow`, so the generic
        :func:`_row_to_job` name-match mapping works directly.
        """
        return super().claim()
