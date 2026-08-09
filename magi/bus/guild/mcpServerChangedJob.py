"""mcpServerChangedJobBoard — MCP server 变更作业。

当 WebUI / LLM manage tool 修改 / 启用 / 停用 / 删除 MCP server 时，
调用方 publish 到本 board；:class:`~magi.mcp.worker.McpWorker` 是唯一
的 consumer，claim 后**写库 + 重连 / 断开 / 重新注入 tools 到
registry**，并 submit :class:`McpServerChangedResult`。

与 ``changeProviderConfigJobBoard`` 的区分
------------------------------------------

- ``changeProviderConfigJob`` 专门服务 provider 配置变更，只有一个
  claimer（provider worker）。
- ``mcpServerChangedJob`` 专门服务 MCP server 配置变更，只有一个
  claimer（mcp worker），与上述正交。

设计要点
========

- **Worker 是 Book 的唯一写者**：manage 工具只 publish Job，不直写
  ``McpServerBook``。Worker claim 后负责
  ``book.delete_by_name`` / ``book.upsert(server)`` /
  ``book.update(server_id, enabled=...)``。这样配置写入与连接
  reload 在同一个事务边界里 — 要么都成功要么都回滚 — LLM 工具
  与 operator 之间的状态永远一致。
- **payload 携带**：
  - ``kind="added"`` / ``kind="updated"`` 必须带完整的
    :class:`~magi.bus.library.local.mcpServerBook.McpServer`
    payload（存为 JSON 列）。
  - ``kind="toggled"`` 必须带 ``new_enabled: bool``。
  - ``kind="deleted"`` 只需 ``server_name``。
- **结果回执**：通过 ``submit_result(McpServerChangedResult)`` 落库
  status / completed_at / error；调用方按
  :meth:`BaseJobBoard.get_result` 轮询，或
  :meth:`BaseJobBoard.wait_for_result` 阻塞到完成。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.guild.base import BaseJobBoard, new_job_id

if TYPE_CHECKING:
    from magi.bus.library.local.mcpServerBook import McpServer

logger = logging.getLogger("magi.bus.guild.mcpServerChangedJob")


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

    Payload shape by ``kind``:

    - ``"added"`` / ``"updated"``: ``server`` must carry the full
      :class:`McpServer` DTO; the worker upserts the row from it.
    - ``"toggled"``: ``new_enabled`` must be set; the worker flips
      the row's ``enabled`` flag.
    - ``"deleted"``: only ``server_name`` is needed; the worker
      deletes the row.
    """

    kind: str
    server_name: str
    server: McpServer | None = None
    new_enabled: bool | None = None
    job_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(
                f"invalid kind {self.kind!r}; expected one of {sorted(VALID_KINDS)}"
            )
        if not self.server_name:
            raise ValueError("server_name is required")
        if self.kind in ("added", "updated") and self.server is None:
            raise ValueError(
                f"kind={self.kind!r} requires a McpServer payload"
            )
        if self.kind == "toggled" and self.new_enabled is None:
            raise ValueError("kind='toggled' requires new_enabled flag")


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

    #: JSON-serialised :class:`McpServer` DTO. Populated for
    #: ``kind="added"`` / ``kind="updated"``; ``None`` for the
    #: other kinds. Stored as a single JSON column rather than
    #: one column per field so the row layout stays decoupled
    #: from the DTO schema.
    server_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
    )

    #: New ``enabled`` flag value for ``kind="toggled"``; ``None``
    #: for the other kinds.
    new_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


# -- payload helpers -----------------------------------------------------


def _dump_server(server: McpServer) -> dict[str, Any]:
    """Serialise a :class:`McpServer` DTO to a JSON-safe dict.

    Includes every DTO field (including the auto-increment ``id``
    and the timestamps) so the Worker can re-upsert without
    losing anything.
    """
    return {
        "id": server.id,
        "name": server.name,
        "connection_type": server.connection_type,
        "command": server.command,
        "args": list(server.args),
        "url": server.url,
        "env": dict(server.env),
        "headers": dict(server.headers),
        "enabled": server.enabled,
        "connect_timeout": server.connect_timeout,
        "execute_timeout": server.execute_timeout,
        "sse_read_timeout": server.sse_read_timeout,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
        "config": dict(server.config),
    }


def _load_server(payload: dict[str, Any]) -> McpServer:
    """Inverse of :func:`_dump_server`."""
    from magi.bus.library.local.mcpServerBook import McpServer

    return McpServer(
        id=int(payload.get("id", 0) or 0),
        name=payload["name"],
        connection_type=payload["connection_type"],
        command=payload.get("command"),
        args=tuple(payload.get("args") or []),
        url=payload.get("url"),
        env=dict(payload.get("env") or {}),
        headers=dict(payload.get("headers") or {}),
        enabled=bool(payload.get("enabled", True)),
        connect_timeout=payload.get("connect_timeout"),
        execute_timeout=payload.get("execute_timeout"),
        sse_read_timeout=payload.get("sse_read_timeout"),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        config=dict(payload.get("config") or {}),
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
        """插入一行变更 job；自动生成 ``job_id``（如果未提供）。

        Serialises ``job.server`` (if any) to the ``server_payload``
        JSON column and copies ``new_enabled`` across verbatim.
        """
        job_id = job.job_id or new_job_id()
        server_payload = _dump_server(job.server) if job.server is not None else None
        with self._session() as s:
            row = _McpServerChangedRow(
                job_id=job_id,
                status="pending",
                kind=job.kind,
                server_name=job.server_name,
                server_payload=server_payload,
                new_enabled=job.new_enabled,
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

    def claim(self) -> McpServerChangedJob | None:
        """Claim the next pending job, materialising the payload
        columns into the :class:`McpServerChangedJob` DTO.

        Mirrors :meth:`BaseJobBoard.claim` but adds the
        ``server_payload`` → :class:`McpServer` and ``new_enabled``
        deserialisation so the Worker sees a fully populated DTO.
        """
        with self._session() as s:
            row = self._claim(s)
            s.commit()
            if row is None:
                return None
            server = (
                _load_server(row.server_payload)
                if row.server_payload is not None
                else None
            )
            return McpServerChangedJob(
                kind=row.kind,
                server_name=row.server_name,
                server=server,
                new_enabled=row.new_enabled,
                job_id=row.job_id,
            )


__all__ = [
    "McpServerChangedJob",
    "McpServerChangedResult",
    "VALID_KINDS",
    "mcpServerChangedJobBoard",
]
