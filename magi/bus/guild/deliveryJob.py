"""deliveryJobBoard — 出站投递作业。

agent 产出回复 → 入队 → worker 投递到渠道
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.guild.base import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin

if TYPE_CHECKING:
    from magi.bus.library.local.conversationBook import MessageBook

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryJob(BaseJob):
    """一次出站投递请求 — agent 产出回复后入队，对应渠道 worker claim 后送出。

    ``channel`` 决定哪个渠道 worker claim（``tg`` / ``webui`` / ...
    各自一个 :meth:`deliveryJobBoard.claim_for_channel` 调用）；
    ``destination`` 可选——直接渠道（如 WebUI WS 单 session）
    可能靠 connection 隐式寻址，不强制给地址。

    投递内容（``text`` / ``conversation_id`` / ``contact_id``）以
    **类型化字段** 暴露,producer / consumer 看到的是具名属性。
    在 DB 层每个字段都是独立列（见 :class:`_DeliveryJobRow`）,
    没有 ``payload`` JSON 黑盒——ORM 行 → dataclass 直接按字段
    名映射。
    """

    channel: str  # 投递渠道（tg/webui/...）
    text: str = ""  # 投递文本（最终回复 / send_message 文本）
    conversation_id: int | None = None  # 关联会话 id（webui worker 用）
    contact_id: int | None = None  # 关联 contact（webui worker 用）
    destination: str | None = None  # 目标地址（chat_id 等）


@dataclass(frozen=True, slots=True)
class DeliveryResult(BaseJobResult):
    """:class:`DeliveryJob` 的投递回执 — 渠道 worker 实际送出后写入。

    :attr:`JobStatus.COMPLETED` 表示 SDK 已确认收到 / WS 已发送完毕。
    :attr:`JobStatus.FAILED` 仅由实际处理该 job 的 worker 提交；BUS 不会因
    lease 重复或超时自行把 delivery 写成失败。
    """


class _DeliveryJobRow(BaseJobRowMixin):
    __tablename__ = "delivery_jobs"
    __table_args__ = {"extend_existing": True}

    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # Delivery content — formerly a single ``payload`` JSON blob. First-class
    # typed columns on :class:`DeliveryJob` so producers / consumers see
    # one field per attribute (no ``payload`` dict).
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class deliveryJobBoard(BaseJobBoard[_DeliveryJobRow, DeliveryJob, DeliveryResult]):
    """Queue (write + claim + submit_result) for outbound deliveries.

    Mirrors :class:`chatNotifyBoard`'s "single chokepoint" contract:
    :meth:`publish` is the *only* code path that writes an assistant
    row into ``chat_messages``. Channel workers (TG / WebUI / …) only
    deliver to the wire; they no longer touch ``messages_book``.

    The row is written **before** the delivery job hits the queue, not
    after the wire delivery succeeds. Trade-off: if the wire delivery
    later fails (worker submits :attr:`JobStatus.FAILED`), the assistant
    row stays in ``chat_messages`` — the transcript reflects what the
    agent said, not what the wire confirmed. Mirrors how the inbound
    side writes the user row at enqueue time regardless of downstream
    LLM / tool success.
    """

    job_model = _DeliveryJobRow
    job_cls = DeliveryJob
    result_cls = DeliveryResult

    def __init__(
        self,
        factory,  # type: ignore[no-untyped-def]
        *,
        messages_book: MessageBook | None = None,
    ) -> None:
        super().__init__(factory)
        # Optional so unit tests can build a board without the local
        # messages store; in that case :meth:`publish` silently skips
        # the assistant-row write. Production wiring lives in
        # :func:`magi.bus.bootstrap.open_bus`.
        self._messages_book = messages_book

    def publish(self, job: DeliveryJob) -> int:
        """Enqueue one delivery and persist the assistant message.

        Single chokepoint for outbound delivery — every path that
        enqueues a delivery (agent worker's final reply, the LLM's
        ``send_message`` tool, future proactive nudges) goes through
        here. The job's ``text`` is mirrored into ``chat_messages``
        with ``role="assistant"`` so the next inbound turn's LLM
        transcript read sees the reply immediately.

        Steps:

          1. Insert the delivery job row (via :meth:`BaseJobBoard.publish`).
          2. Best-effort ``messages_book.add(Message(role="assistant", …))``.
             ``ConversationNotFoundError`` (e.g. ``conversation_id`` is
             ``None``) and any other failure is logged and swallowed —
             the delivery job is already enqueued; a transient
             ``messages_book`` blip must not block outbound delivery.

        Returns the *job_id* of the published job (Board-generated).
        """
        job_id = super().publish(job)
        if self._messages_book is not None:
            try:
                from magi.bus.library.local.conversationBook import Message

                self._messages_book.add(Message(
                    conversation_id=job.conversation_id or 0,
                    role="assistant",
                    text=job.text,
                ))
            except Exception:
                logger.exception(
                    "deliveryJobBoard.publish: messages_book.add failed "
                    "(conversation=%s, channel=%s); deliveryJob %s enqueued without row",
                    job.conversation_id,
                    job.channel,
                    job_id,
                )
        return job_id

    def claim_for_channel(self, *, channel: str, worker_id: str) -> DeliveryJob | None:
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
                owner=self._require_worker_id(worker_id),
                extra_where=[_DeliveryJobRow.channel == channel],
            )
            s.commit()
            if row is None:
                return None
            fresh = s.get(_DeliveryJobRow, row.job_id)
            return self._map_row(fresh, self.job_cls) if fresh else None
