"""chatJob — 聊天消息作业。

public:  ChatJob (入参), ChatJobResult (出参) — 平级 dataclass
internal: _ChatJobRow (ORM) — 数据库实现细节
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobQueue


# -- public dataclasses ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class ChatJob:
    """publisher 发布 / worker 认领。"""
    text: str
    conversation_id: str
    channel: str = ""
    metadata: dict | None = None
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class ChatJobResult:
    """worker 提交 / publisher 轮询。"""
    job_id: str
    success: bool
    reply: str | None = None
    error: str | None = None


# -- internal ORM ----------------------------------------------------------

class _ChatJobRow(Base):
    __tablename__ = "chat_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # 请求
    text: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), default="")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    # 租约
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 结果
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Queue -----------------------------------------------------------------

class chatJob(BaseJobQueue[_ChatJobRow, ChatJob, ChatJobResult]):
    job_model = _ChatJobRow
    job_cls = ChatJob
    result_cls = ChatJobResult

    def publish(self, job: ChatJob) -> str:
        """发布聊天作业，返回 job_id。"""
        with self._factory.session() as s:
            row = _ChatJobRow(
                job_id=uuid.uuid4().hex,
                status="pending",
                text=job.text,
                conversation_id=job.conversation_id,
                channel=job.channel,
                metadata_json=job.metadata,
            )
            s.add(row)
            s.flush()
            s.commit()
            return row.job_id
