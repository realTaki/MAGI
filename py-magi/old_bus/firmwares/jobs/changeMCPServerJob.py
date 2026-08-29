"""changeMCPServerJobBoard — MCP server 变更作业。

当 WebUI / LLM manage tool 修改 / 启用 / 停用 / 删除 MCP server 时，
调用方 publish 到本 board；:class:`~mcp.worker.McpWorker` 是唯一
的 consumer，claim 后**写库 + 重连 / 断开 / 重新注入 tools 到
registry**，并 submit :class:`ChangeMCPServerResult`。

与 ``changeProviderConfigJobBoard`` 的区分
------------------------------------------

- ``changeProviderConfigJob`` 专门服务 provider 配置变更，只有一个
  claimer（provider worker）。
- ``changeMCPServerJob`` 专门服务 MCP server 配置变更，只有一个
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
  - :attr:`MCPKind.ADDED` / :attr:`MCPKind.UPDATED` 必须带完整的
    :class:`~bus.firmwares.books.local.mcpServerBook.McpServer`
    payload（存为 JSON 列）。
  - :attr:`MCPKind.TOGGLED` 必须带 ``new_enabled: bool``。
  - :attr:`MCPKind.DELETED` 只需 ``server_name``。
- **结果回执**：通过 ``submit_result(ChangeMCPServerResult)`` 落库
  status / completed_at / error；调用方按
  :meth:`BaseJobBoard.get_result` 轮询，或
  :meth:`BaseJobBoard.wait_for_result` 阻塞到完成。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.db.base import enum_column
from old_bus.bases.job import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin, JobStatus

if TYPE_CHECKING:
    from old_bus.firmwares.books.local.mcpServerBook import McpServer

logger = logging.getLogger("bus.firmwares.jobs.changeMCPServerJob")


# -- public enum ---------------------------------------------------------


class MCPKind(StrEnum):
    """Change-kind discriminator stored on :class:`ChangeMCPServerJob.kind`.

    * ``MCPKind.ADDED``   — new server registered; the worker
      ``book.upsert(server)`` and connects.
    * ``MCPKind.UPDATED`` — server DTO changed; the worker
      ``book.upsert(server)`` and reconnects.
    * ``MCPKind.DELETED`` — server removed; the worker
      ``book.delete_by_name`` and disconnects.
    * ``MCPKind.TOGGLED`` — single ``enabled`` flag flip; the
      worker ``book.update(server_id, enabled=...)`` and reloads.

    ``StrEnum`` rather than bare string constants so typos are
    caught at lookup time instead of silently comparing False:
    every member is still a ``str``
    (``MCPKind.ADDED == "added"``), so ORM columns, JSON
    serialisation, ``==`` / ``!=`` against string literals and
    existing rows keep working unchanged. Mirrors
    :class:`bus.firmwares.books.local.contactBook.NoteKind` /
    :class:`bus.firmwares.books.local.memoryBook.MemoryKind`.
    See ``docs/insights/ENUM_MIGRATION_INVENTORY.md`` §2.
    """

    ADDED = "added"
    UPDATED = "updated"
    DELETED = "deleted"
    TOGGLED = "toggled"


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeMCPServerJob(BaseJob):
    """一次 MCP 服务器配置变更事件。

    ``kind`` 取自 :class:`MCPKind`；``server_name`` 是操作
    目标的主键（与 ``mcp_servers.name`` 对齐）。

    Payload shape by ``kind``:

    - :attr:`MCPKind.ADDED` / :attr:`MCPKind.UPDATED`: ``server``
      must carry the full :class:`McpServer` DTO; the worker
      upserts the row from it.
    - :attr:`MCPKind.TOGGLED`: ``new_enabled`` must be set; the
      worker flips the row's ``enabled`` flag.
    - :attr:`MCPKind.DELETED`: only ``server_name`` is needed;
      the worker deletes the row.
    """

    kind: MCPKind  # 变更类型（added/updated/toggled/deleted，取自 MCPKind）
    server_name: str  # 目标 MCP server 的主键（与 mcp_servers.name 对齐）
    server: McpServer | None = None  # 完整 DTO（kind in {ADDED,UPDATED} 时必填；存为 JSON 列）
    new_enabled: bool | None = None  # 新的 enabled 标志（kind=TOGGLED 时必填）

    def __post_init__(self) -> None:
        if self.kind not in MCPKind:
            raise ValueError(
                f"invalid kind {self.kind!r}; expected one of "
                f"{sorted(k.value for k in MCPKind)!r}"
            )
        if not self.server_name:
            raise ValueError("server_name is required")
        if self.kind in (MCPKind.ADDED, MCPKind.UPDATED) and self.server is None:
            raise ValueError(f"kind={self.kind!r} requires a McpServer payload")
        if self.kind == MCPKind.TOGGLED and self.new_enabled is None:
            raise ValueError("kind=MCPKind.TOGGLED requires new_enabled flag")


@dataclass(frozen=True, slots=True)
class ChangeMCPServerResult(BaseJobResult):
    """Worker 处理结果的回执。"""


# -- internal ORM --------------------------------------------------------


class _ChangeMCPServerRow(BaseJobRowMixin):
    __tablename__ = "change_mcp_server_jobs"
    __table_args__ = {"extend_existing": True}

    kind: Mapped[MCPKind] = mapped_column(enum_column(MCPKind), nullable=False)
    server_name: Mapped[str] = mapped_column(Text, nullable=False)

    #: JSON-serialised :class:`McpServer` DTO. Populated for
    #: :attr:`MCPKind.ADDED` / :attr:`MCPKind.UPDATED`; ``None``
    #: for the other kinds. Stored as a single JSON column
    #: rather than one column per field so the row layout stays
    #: decoupled from the DTO schema.
    server_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    #: New ``enabled`` flag value for :attr:`MCPKind.TOGGLED`;
    #: ``None`` for the other kinds.
    new_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


# -- payload helpers -----------------------------------------------------


def _dump_server(server: McpServer) -> dict[str, Any]:
    """Serialise a :class:`McpServer` DTO to a JSON-safe dict.

    Only carries the business fields the Worker needs to re-upsert
    via :meth:`McpServerBook.upsert`. The DB-owned ``id`` and audit
    timestamps are deliberately omitted — ``upsert`` locates the row
    by ``name`` and lets the ORM (re)assign those values.
    """
    return {
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
        "config": dict(server.config),
    }


def _load_server(payload: dict[str, Any]) -> McpServer:
    """Inverse of :func:`_dump_server`."""
    from old_bus.firmwares.books.local.mcpServerBook import McpServer

    return McpServer(
        name=payload["name"],
        connection_type=payload["connection_type"],
        command=payload.get("command"),
        args=list(payload.get("args") or []),
        url=payload.get("url"),
        env=dict(payload.get("env") or {}),
        headers=dict(payload.get("headers") or {}),
        enabled=bool(payload.get("enabled", True)),
        connect_timeout=payload.get("connect_timeout"),
        execute_timeout=payload.get("execute_timeout"),
        sse_read_timeout=payload.get("sse_read_timeout"),
        config=dict(payload.get("config") or {}),
    )


# -- Board ---------------------------------------------------------------


class changeMCPServerJobBoard(
    BaseJobBoard[_ChangeMCPServerRow, ChangeMCPServerJob, ChangeMCPServerResult]
):
    """MCP 服务器变更作业板（claim → 处理 → submit_result → get_result）。"""

    job_model = _ChangeMCPServerRow
    job_cls = ChangeMCPServerJob
    result_cls = ChangeMCPServerResult

    def publish(self, job: ChangeMCPServerJob) -> int:
        """插入一行变更 job；数据库生成 ``job_id``，调用方无法指定。

        Serialises ``job.server`` (if any) to the ``server_payload``
        JSON column and copies ``new_enabled`` across verbatim.
        """
        server_payload = _dump_server(job.server) if job.server is not None else None
        with self._session() as s:
            row = _ChangeMCPServerRow(
                status=JobStatus.PENDING,
                kind=job.kind,
                server_name=job.server_name,
                server_payload=server_payload,
                new_enabled=job.new_enabled,
            )
            s.add(row)
            s.flush()
            s.commit()
        logger.info(
            "changeMCPServerJob: published kind=%s name=%s job_id=%s",
            job.kind,
            job.server_name,
            row.job_id,
        )
        return row.job_id

    def claim(self, *, worker_id: str) -> ChangeMCPServerJob | None:
        """Claim the next pending job, materialising the payload
        columns into the :class:`ChangeMCPServerJob` DTO.

        Mirrors :meth:`BaseJobBoard.claim` but adds the
        ``server_payload`` → :class:`McpServer` and ``new_enabled``
        deserialisation so the Worker sees a fully populated DTO.
        """
        worker_id = self._require_worker_id(worker_id)
        with self._session() as s:
            row = self._claim(s, worker_id=worker_id)
            s.commit()
            if row is None:
                return None
            server = _load_server(row.server_payload) if row.server_payload is not None else None
            job = ChangeMCPServerJob(
                kind=MCPKind(row.kind),
                server_name=row.server_name,
                server=server,
                new_enabled=row.new_enabled,
            )
            object.__setattr__(job, "job_id", row.job_id)  # init=False，frozen 下回填
            return job


__all__ = [
    "MCPKind",
    "ChangeMCPServerJob",
    "ChangeMCPServerResult",
    "changeMCPServerJobBoard",
]
