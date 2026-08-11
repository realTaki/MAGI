"""McpServerBook — operator-configured MCP server rows.

Schema for the ``mcp_servers`` table.
The ORM maps the same physical SQLite table with flat columns.
``__table_args__ = {"extend_existing": True}`` lets multiple
ORMs share the table without ``MetaData`` collisions when each
is registered in a different import order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook

# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class McpServer:
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

    id: int  # 自增主键
    name: str  # 操作员面向的唯一名（PK）
    connection_type: str  # 连接类型（stdio/sse/streamable_http）
    command: str | None = None  # stdio 启动命令
    args: tuple[str, ...] = ()  # stdio 启动参数
    url: str | None = None  # URL 类型连接的端点
    env: dict[str, str] = field(default_factory=dict)  # stdio 进程环境变量
    headers: dict[str, str] = field(default_factory=dict)  # HTTP 自定义请求头
    enabled: bool = True  # 是否启用（worker 会据此决定是否连接）
    connect_timeout: float | None = None  # 连接超时（None=使用全局默认值）
    execute_timeout: float | None = None  # 工具调用执行超时
    sse_read_timeout: float | None = None  # SSE 流读取超时
    created_at: str | None = None  # 创建时间（ISO 8601）
    updated_at: str | None = None  # 最近更新时间（ISO 8601）

    # Preserved on the row but not exposed on the DTO: the
    # ``config`` JSON blob is reserved for future read-only
    # callers that want a single denormalised payload. New
    # writes always go through the dedicated columns.
    config: dict[str, Any] = field(default_factory=dict)  # 预留的 JSON 配置块（未来只读）


# -- internal ORM --------------------------------------------------------


class _McpServerRow(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ``name`` is the operator-facing id and the PK. It
    # is unique but NOT a SQLAlchemy ``primary_key`` because
    # SQLite refuses autoincrement on composite primary keys.
    # The cross-ORM uniqueness contract lives in the
    # ``UniqueConstraint`` below.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # STDIO
    command: Mapped[str | None] = mapped_column(String(256), nullable=True)
    args_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
    )
    env_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default="{}",
    )

    # URL-based (sse / streamable_http)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    headers_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
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

    created_at: Mapped[DateTime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        nullable=False,
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
        nullable=False,
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


def _parse_json_dict(raw: str | None) -> dict[str, str]:
    """Defensive JSON-object parser for ``env_json`` / ``headers_json``.

    Mirrors ``magi.bus.db.models.local.mcp_server._parse_json_dict``
    — coerce non-string values to ``str`` rather than dropping
    them, so a bad row logs and degrades to empty rather than
    crashing the consumer.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _parse_json_list(raw: str | None) -> list[str]:
    """Defensive JSON-array parser for ``args_json`` — coerce
    non-string values to ``str`` and drop anything we can't
    serialise."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


# -- Book ----------------------------------------------------------------


class McpServerBook(BaseBook[_McpServerRow, McpServer]):
    model_cls = _McpServerRow
    dto_cls = McpServer

    def get(self, *, server_id: int) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(select(_McpServerRow).where(_McpServerRow.id == server_id))
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(select(_McpServerRow).where(_McpServerRow.name == name))
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[McpServer]:
        with self._session() as s:
            rows = s.scalars(select(_McpServerRow).order_by(_McpServerRow.name)).all()
            return [self._row_to_dto(r) for r in rows]

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
            return [self._row_to_dto(r) for r in rows]

    def add(
        self,
        *,
        name: str,
        connection_type: str,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
        connect_timeout: float | None = None,
        execute_timeout: float | None = None,
        sse_read_timeout: float | None = None,
    ) -> McpServer:
        with self._session() as s:
            row = _McpServerRow(
                name=name,
                connection_type=connection_type,
                command=command,
                args_json=json.dumps(args or []),
                env_json=json.dumps(env or {}),
                url=url,
                headers_json=json.dumps(headers or {}),
                enabled=enabled,
                connect_timeout=connect_timeout,
                execute_timeout=execute_timeout,
                sse_read_timeout=sse_read_timeout,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update(
        self,
        *,
        server_id: int,
        # ``connection_type`` and ``enabled`` are NOT NULL columns; the
        # ``_UnsetType`` sentinel is the only legal "leave alone" value.
        # Nullable columns also accept ``None`` to clear the value.
        connection_type: str | _UnsetType = _UNSET,
        command: str | None | _UnsetType = _UNSET,
        args: list[str] | None | _UnsetType = _UNSET,
        env: dict[str, str] | None | _UnsetType = _UNSET,
        url: str | None | _UnsetType = _UNSET,
        headers: dict[str, str] | None | _UnsetType = _UNSET,
        enabled: bool | _UnsetType = _UNSET,
        connect_timeout: float | None | _UnsetType = _UNSET,
        execute_timeout: float | None | _UnsetType = _UNSET,
        sse_read_timeout: float | None | _UnsetType = _UNSET,
    ) -> None:
        """In-place update of a row by numeric id.

        Every column defaults to :data:`_UNSET` ("leave alone").
        Pass ``None`` to clear the column (transport-type
        switches rely on this to drop a stale ``command`` /
        ``url`` etc.); pass a value to overwrite.

        The :meth:`upsert` wrapper is the primary entry point
        for full-row rewrites.
        """
        with self._session() as s:
            row = s.scalar(select(_McpServerRow).where(_McpServerRow.id == server_id))
            if row is None:
                return
            if connection_type is not _UNSET:
                # ``cast`` for Pylance — the ``is not _UNSET`` check
                # already guarantees the type, but the singleton class
                # pattern confuses the type checker so the union isn't
                # narrowed automatically.
                row.connection_type = cast(str, connection_type)
            if command is not _UNSET:
                row.command = cast("str | None", command)
            if args is not _UNSET:
                row.args_json = json.dumps(args or [])
            if env is not _UNSET:
                row.env_json = json.dumps(env or {})
            if url is not _UNSET:
                row.url = cast("str | None", url)
            if headers is not _UNSET:
                row.headers_json = json.dumps(headers or {})
            if enabled is not _UNSET:
                row.enabled = cast(bool, enabled)
            if connect_timeout is not _UNSET:
                row.connect_timeout = cast("float | None", connect_timeout)
            if execute_timeout is not _UNSET:
                row.execute_timeout = cast("float | None", execute_timeout)
            if sse_read_timeout is not _UNSET:
                row.sse_read_timeout = cast("float | None", sse_read_timeout)
            s.commit()

    def delete(self, *, server_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(select(_McpServerRow).where(_McpServerRow.id == server_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

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
        if connection_type not in ("stdio", "sse", "streamable_http"):
            raise ValueError("connection_type must be one of: stdio, sse, streamable_http")
        if connection_type == "stdio" and not (command and command.strip()):
            raise ValueError("stdio servers require 'command'")
        if connection_type != "stdio" and not (url and url.strip()):
            raise ValueError(f"{connection_type} servers require 'url'")

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
            return self.add(
                name=name,
                connection_type=connection_type,
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
        # When the transport type is changing, force-clear the
        # fields that don't belong to the new type so a previous
        # stdio ``command`` doesn't linger after switching to
        # streamable_http (the worker keys tool discovery on
        # ``connection_type``; stale fields would silently mask
        # transport errors).
        new_connection_type = (
            connection_type if connection_type is not None else existing.connection_type
        )
        self.update(
            server_id=existing.id,
            connection_type=connection_type,
            command=None if new_connection_type != "stdio" else command,
            args=None if new_connection_type != "stdio" else args_list,
            env=None if new_connection_type != "stdio" else env_dict,
            url=None if new_connection_type == "stdio" else url,
            headers=None if new_connection_type == "stdio" else headers_dict,
            enabled=enabled,
            connect_timeout=connect_timeout,
            execute_timeout=execute_timeout,
            sse_read_timeout=sse_read_timeout,
        )
        return self.get_by_name(name=name)  # type: ignore[return-value]

    def delete_by_name(self, *, name: str) -> bool:
        """Delete a row by operator-facing name.

        Returns ``False`` when the row doesn't exist — idempotent
        for the LLM tool's retry semantics.
        """
        existing = self.get_by_name(name=name)
        if existing is None:
            return False
        return self.delete(server_id=existing.id)

    def toggle(self, *, name: str) -> McpServer | None:
        """Flip the ``enabled`` flag for a row by name.

        Returns ``None`` when the row doesn't exist; the LLM
        tool turns that into a 404 envelope.
        """
        existing = self.get_by_name(name=name)
        if existing is None:
            return None
        new_enabled = not existing.enabled
        # ``_UNSET`` keeps every other column alone — ``None``
        # would now mean "clear this field", which would
        # wipe the connection_type / command / url on a
        # simple enable-flip.
        self.update(
            server_id=existing.id,
            connection_type=_UNSET,
            command=_UNSET,
            args=_UNSET,
            env=_UNSET,
            url=_UNSET,
            headers=_UNSET,
            enabled=new_enabled,
            connect_timeout=_UNSET,
            execute_timeout=_UNSET,
            sse_read_timeout=_UNSET,
        )
        return self.get_by_name(name=name)

    # -- DTO mapping ----------------------------------------------------

    def _row_to_dto(self, row: _McpServerRow) -> McpServer:
        # Mirror the base class' field-by-field mapping, then
        # overlay the JSON-deserialised args / env / headers and
        # the (read-only) ``config`` blob. The base class falls
        # back to ``hasattr`` for every field, so missing
        # attributes silently become ``None`` — we don't rely
        # on that here.
        kwargs: dict = {
            "id": row.id,
            "name": row.name,
            "connection_type": row.connection_type,
            "command": row.command,
            "url": row.url,
            "enabled": bool(row.enabled),
            "config": dict(row.config or {}),
        }
        for f_name, raw in (
            ("args", row.args_json),
            ("env", row.env_json),
            ("headers", row.headers_json),
        ):
            if f_name == "args":
                kwargs[f_name] = tuple(_parse_json_list(raw))
            else:
                kwargs[f_name] = _parse_json_dict(raw)
        for ts_name in (
            "connect_timeout",
            "execute_timeout",
            "sse_read_timeout",
            "created_at",
            "updated_at",
        ):
            value = getattr(row, ts_name, None)
            if isinstance(value, DateTime):
                # SQLAlchemy's DateTime column type is structurally
                # identical to ``datetime.datetime`` at runtime (the
                # value is loaded from a row that's already been
                # refreshed via ``s.refresh(row)`` above), but the
                # stub types ``value`` as the column class. Cast to
                # the underlying stdlib type so ``tzinfo`` /
                # ``isoformat`` / ``astimezone`` resolve statically.
                dt = cast(datetime, value)
                if dt.tzinfo is None:
                    kwargs[ts_name] = dt.isoformat() + "Z"
                else:
                    kwargs[ts_name] = dt.astimezone().isoformat().replace("+00:00", "Z")
            elif value is None:
                kwargs[ts_name] = None
            else:
                kwargs[ts_name] = value
        return McpServer(**kwargs)


# -- public serialiser ---------------------------------------------------
#
# Lives next to the DTO so the wire shape evolves together
# with the row schema. The LLM manage tools (see
# :mod:`magi.tools.mcp`) import this directly; nothing in the
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
    sees the same shape every time. ``args`` is normalised to
    a list (the DTO stores it as a tuple) so the wire shape
    doesn't leak the internal ``tuple`` choice.
    """
    return {
        "name": server.name,
        "connection_type": server.connection_type,
        "command": server.command,
        "args": list(server.args),
        "url": server.url,
        "enabled": server.enabled,
        "connect_timeout": server.connect_timeout,
        "execute_timeout": server.execute_timeout,
        "sse_read_timeout": server.sse_read_timeout,
    }


__all__ = ["McpServer", "McpServerBook", "_McpServerRow", "serialize_mcp_server"]
