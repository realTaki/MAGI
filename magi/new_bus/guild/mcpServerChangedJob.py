"""mcpServerChangedJobBoard — MCP server 变更作业。

当 WebUI / LLM manage tool 修改 / 启用 / 停用 / 删除 MCP server 时，
API 侧 publish 到本 board；:class:`~magi.mcp.worker.McpWorker` 是唯一
的 consumer，claim 后重连 / 断开 / 重新注入 tools 到 registry，并 submit
:class:`McpServerChangedResult`。

与 ``controlJobBoard`` / ``changeProviderConfigJobBoard`` 的区分
-------------------------------------------------------

- ``controlJob`` 是 generic 的运行时信号 channel，多个 worker 都
  可能 claim。
- ``changeProviderConfigJob`` 专门服务 provider 配置变更，只有一个
  claimer（provider worker）。
- ``mcpServerChangedJob`` 专门服务 MCP 服务器配置变更，只有一个
  claimer（mcp worker），与上述两者正交。

设计要点
========

- **self-contained write（本阶段不做）**：当前 WebUI / LLM manage tool
  仍直写旧 bus ``McpService``，未在此 board 上 publish。Worker 启动
  时已经能从 ``mcp_servers_book`` 读到全量数据；运行期 Job Board
  空转直到 API / manage 工具迁移到 new_bus。
- **命名**：``mcpServerChanged`` → "MCP 服务器配置变更"（一个变更
  事件对应一行 job）。
- **结果回执**：通过 ``submit_result(McpServerChangedResult)`` 落库
  status / completed_at / error；调用方按 :meth:`BaseJobBoard.get_result`
  轮询。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.guild.base import BaseJobBoard, new_job_id

if TYPE_CHECKING:
    pass

logger = logging.getLogger("magi.new_bus.guild.mcpServerChangedJob")


# -- public dataclasses --------------------------------------------------


#: Allowed ``kind`` values. ``toggled`` maps to a single flag flip
#: in the worker; the other three carry the same "reload this
#: server" semantics so the worker uses a single code path.
VALID_KINDS: frozenset[str] = frozenset({"added", "updated", "deleted", "toggled"})


@dataclass(frozen=True, slots=True)
class McpServerChangedJob:
    """一次 MCP 服务器配置变更事件。

    ``kind`` 取自 :data:`VALID_KINDS`；``server_name`` 是操作
    目标的主键（与 ``mcp_servers.name`` 对齐）。
    """

    kind: str
    server_name: str
    job_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(
                f"invalid kind {self.kind!r}; expected one of {sorted(VALID_KINDS)}"
            )
        if not self.server_name:
            raise ValueError("server_name is required")


@dataclass(frozen=True, slots=True)
class McpServerChangedResult:
    """Worker 处理结果的回执。"""

    job_id: str
    success: bool
    error: str | None = None


# -- internal ORM --------------------------------------------------------


class _McpServerChangedRow(Base):
    __tablename__ = "mcp_server_changed_jobs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    server_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


# -- Board ---------------------------------------------------------------


class mcpServerChangedJobBoard(
    BaseJobBoard[_McpServerChangedRow, McpServerChangedJob, McpServerChangedResult]
):
    """MCP 服务器变更作业板（claim → 处理 → submit_result → get_result）。"""

    job_model = _McpServerChangedRow
    job_cls = McpServerChangedJob
    result_cls = McpServerChangedResult

    def publish(self, job: McpServerChangedJob) -> str:
        """插入一行变更 job；自动生成 ``job_id``（如果未提供）。"""
        job_id = job.job_id or new_job_id()
        with self._session() as s:
            row = _McpServerChangedRow(
                job_id=job_id,
                status="pending",
                kind=job.kind,
                server_name=job.server_name,
            )
            s.add(row)
            s.flush()
            s.commit()
        logger.info(
            "mcpServerChangedJob: published kind=%s name=%s job_id=%s",
            job.kind,
            job.server_name,
            job_id,
        )
        return job_id


__all__ = [
    "McpServerChangedJob",
    "McpServerChangedResult",
    "VALID_KINDS",
    "mcpServerChangedJobBoard",
]
