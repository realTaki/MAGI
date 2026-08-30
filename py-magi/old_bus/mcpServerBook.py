"""McpServerBook — operator-configured MCP server rows.

Schema for the ``mcp_servers`` table.
The ORM maps the same physical SQLite table with flat columns.
``__table_args__ = {"extend_existing": True}`` lets multiple
ORMs share the table without ``MetaData`` collisions when each
is registered in a different import order.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from old_bus.bases.db.base import enum_column


class MCPConnectionType(StrEnum):
    """Transport type discriminator stored on ``McpServer.connection_type``.

    Closed set — adding a transport requires a schema migration. The
    three members are the canonical MCP transports the runtime can
    bootstrap (see :class:`mcp.MCPClient`). Values are the same
    strings the upstream MCP SDK uses, so they round-trip through
    the loader / worker without translation.

    ``StrEnum`` rather than bare constants so typos are caught at
    lookup time instead of silently comparing False: every member
    is still a ``str`` (``MCPConnectionType.STDIO == "stdio"``),
    so ``isinstance(x, str)`` checks, ``json.dumps`` serialisation,
    and existing ``connection_type == "stdio"`` comparisons keep
    working unchanged. Mirrors
    :class:`bus.firmwares.books.local.contactBook.NoteKind`.
    """

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpServer(BaseRecord):
    """One operator-configured MCP server.

    ``id`` is the autoincrement PK inherited from the bus
    schema (kept for future read-only callers that want the
    numeric handle). Operator-facing identity remains ``name``
    (match the PK); ``name`` is immutable — rename by
    delete + create.

    ``args`` / ``env`` / ``headers`` are deserialised from the
    JSON columns on the way out and serialised on the way in.
    The wire shape is identical to the DTO, minus
    the secret values masked at the API layer.
    """

    name: str  # 操作员面向的唯一名（PK）
    connection_type: MCPConnectionType  # 连接类型（stdio/sse/streamable_http）
    command: str | None = None  # stdio 启动命令
    args: list[str] = field(default_factory=list)  # stdio 启动参数
    url: str | None = None  # URL 类型连接的端点
    env: dict[str, str] = field(default_factory=dict)  # stdio 进程环境变量
    headers: dict[str, str] = field(default_factory=dict)  # HTTP 自定义请求头
    enabled: bool = True  # 是否启用（worker 会据此决定是否连接）
    connect_timeout: float | None = None  # 连接超时（None=使用全局默认值）
    execute_timeout: float | None = None  # 工具调用执行超时
    sse_read_timeout: float | None = None  # SSE 流读取超时

    # Preserved on the row but not exposed on the DTO: the
    # ``config`` JSON blob is reserved for future read-only
    # callers that want a single denormalised payload. New
    # writes always go through the dedicated columns.
    config: dict[str, Any] = field(default_factory=dict)  # 预留的 JSON 配置块（未来只读）


# -- internal ORM --------------------------------------------------------


class _McpServerRow(BaseRecordMixin):
    __tablename__ = "mcp_servers"
    __table_args__ = {"extend_existing": True}

    # ``name`` is the operator-facing id and the PK. It
    # is unique but NOT a SQLAlchemy ``primary_key`` because
    # SQLite refuses autoincrement on composite primary keys.
    # The cross-ORM uniqueness contract lives in the
    # ``UniqueConstraint`` below.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    connection_type: Mapped[MCPConnectionType] = mapped_column(
        enum_column(MCPConnectionType), nullable=False
    )

    # STDIO
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    args: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )
    env: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # URL-based (sse / streamable_http)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )

    # Per-server timeouts. ``None`` → worker uses
    # :class:`MCPTimeoutConfig` defaults read from settings_book.
    connect_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    execute_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    sse_read_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Reserved for future read-only callers — writes still go
    # through the dedicated columns. Kept as ``JSON`` to match
    # the ``mcp_server.McpServer`` schema naming.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    # ``name`` is unique per operator; this is the contract the
    # The PK enforces uniqueness. Without it the column would
    # allow duplicate server names — the worker would pick one
    # and silently drop the other.
    __table_args__ = (
        UniqueConstraint("name", name="uq_mcp_servers_name"),
        {"extend_existing": True},
    )


# -- helpers -------------------------------------------------------------


class _UnsetType:
    """Sentinel singleton — pass to :meth:`McpServerBook.update`
    to leave a column alone. Distinct from ``None``, which now
    means "set this column to NULL / empty"."""

    _instance: _UnsetType | None = None

    def __new__(cls) -> _UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _UnsetType()


# -- Book ----------------------------------------------------------------


class McpServerBook(BaseBook[_McpServerRow, McpServer]):
    model_cls = _McpServerRow
    record_cls = McpServer

    def get_by_name(self, *, name: str) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(select(_McpServerRow).where(_McpServerRow.name == name))
            return self.record_cls.from_row(row) if row else None

    def list_all(self) -> list[McpServer]:
        with self._session() as s:
            rows = s.scalars(select(_McpServerRow).order_by(_McpServerRow.name)).all()
            return [self.record_cls.from_row(r) for r in rows]

    def list_enabled(self) -> list[McpServer]:
        """Return every row whose ``enabled`` column is true.

        Mirrors the ``McpService.enabled_configs`` filter
        — disabled rows are skipped so the worker doesn't try
        to connect them at bootstrap.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_McpServerRow)
                .where(_McpServerRow.enabled.is_(True))
                .order_by(_McpServerRow.name)
            ).all()
            return [self.record_cls.from_row(r) for r in rows]

    # -- convenience methods that the worker / future API use --------

    def upsert(
        self,
        *,
        name: str,
        connection_type: str,
        command: str | None = None,
        args: list[str] | tuple[str, ...] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
        connect_timeout: float | None = None,
        execute_timeout: float | None = None,
        sse_read_timeout: float | None = None,
    ) -> McpServer:
        """Insert-or-update a row by name.

        Same shape as ``McpService.upsert`` (which
        is what the WebUI + LLM manage tools still call until
        they migrate to bus). Validates ``connection_type``
        + transport-specific required fields; raises
        :class:`ValueError` on bad input — the existing manage
        tools already catch this in their error envelopes.
        """
        if connection_type not in MCPConnectionType:
            raise ValueError("connection_type must be one of: stdio, sse, streamable_http")
        # Coerce to MCPConnectionType so the typed ``McpServer.connection_type``
        # field (and ``with_changes``) receive the enum value, not a raw str.
        # ``StrEnum(value)`` round-trips a validated string back to its member,
        # so behaviour for str-input callers is unchanged.
        conn_type = MCPConnectionType(connection_type)
        if conn_type == "stdio" and not (command and command.strip()):
            raise ValueError("stdio servers require 'command'")
        if conn_type != "stdio" and not (url and url.strip()):
            raise ValueError(f"{conn_type} servers require 'url'")

        args_list = list(args) if args else []
        env_dict = env or {}
        headers_dict = headers or {}

        existing = self.get_by_name(name=name)
        # Switching transport types (stdio ↔ url-based) should
        # clear the fields that don't apply to the new type.
        # stdio needs command/args/env; url-based needs url/headers.
        # ``None`` here means "caller did not pass this; clear it
        # if it's now stale".
        if existing is None:
            record = McpServer(
                name=name,
                connection_type=conn_type,
                command=command,
                args=args_list,
                env=env_dict,
                url=url,
                headers=headers_dict,
                enabled=enabled,
                connect_timeout=connect_timeout,
                execute_timeout=execute_timeout,
                sse_read_timeout=sse_read_timeout,
            )
            self.add(record)
            return self.get_by_name(name=name)  # type: ignore[return-value]
        # When the transport type is changing, force-clear the
        # fields that don't belong to the new type so a previous
        # stdio ``command`` doesn't linger after switching to
        # streamable_http (the worker keys tool discovery on
        # ``connection_type``; stale fields would silently mask
        # transport errors).
        # ``conn_type`` is the validated enum value above; never ``str`` here
        # because we coerced at the entry. The previous ``is not None``
        # fallback was dead — the public parameter is annotated ``str``.
        new_connection_type = conn_type
        self.update(replace(
            existing,
            connection_type=conn_type,
            command=None if new_connection_type != "stdio" else command,
            args=[] if new_connection_type != "stdio" else args_list,
            env={} if new_connection_type != "stdio" else env_dict,
            url=None if new_connection_type == "stdio" else url,
            headers={} if new_connection_type == "stdio" else headers_dict,
            enabled=enabled,
            connect_timeout=connect_timeout,
            execute_timeout=execute_timeout,
            sse_read_timeout=sse_read_timeout,
        ))
        return self.get_by_name(name=name)  # type: ignore[return-value]

    def delete_by_name(self, *, name: str) -> bool:
        """Delete a row by operator-facing name.

        Returns ``False`` when the row doesn't exist — idempotent
        for the LLM tool's retry semantics.
        """
        existing = self.get_by_name(name=name)
        if existing is None:
            return False
        return self.delete(existing.id)

    def toggle(self, *, name: str) -> McpServer | None:
        """Flip the ``enabled`` flag for a row by name.

        Returns ``None`` when the row doesn't exist; the LLM
        tool turns that into a 404 envelope.
        """
        existing = self.get_by_name(name=name)
        if existing is None:
            return None
        new_enabled = not existing.enabled
        self.update(replace(existing, enabled=new_enabled))
        return self.get_by_name(name=name)

    # -- DTO mapping ----------------------------------------------------


# -- public serialiser ---------------------------------------------------
#
# Lives next to the DTO so the wire shape evolves together
# with the row schema. The LLM manage tools (see
# :mod:`tools.mcp`) import this directly; nothing in the
# loader or the worker reaches for it.
#
# Privacy: ``env`` / ``headers`` carry API keys / tokens and
# are intentionally **never** serialised — the operator can
# inspect them in the WebUI; the LLM doesn't need them and
# shouldn't see them.


def serialize_mcp_server(server: McpServer) -> dict[str, Any]:
    """Render a bus :class:`McpServer` DTO into a JSON-safe dict.

    Field order is stable (the operator-facing identity fields
    first, then connection details, then timeouts) so the LLM
    sees the same shape every time.
    """
    return {
        "name": server.name,
        "connection_type": server.connection_type,
        "command": server.command,
        "args": server.args,
        "url": server.url,
        "enabled": server.enabled,
        "connect_timeout": server.connect_timeout,
        "execute_timeout": server.execute_timeout,
        "sse_read_timeout": server.sse_read_timeout,
    }


__all__ = [
    "MCPConnectionType",
    "McpServer",
    "McpServerBook",
    "_McpServerRow",
    "serialize_mcp_server",
]
