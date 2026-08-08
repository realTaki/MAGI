"""McpServerBook — operator-configured MCP server rows.

Schema mirrors the old bus's ``mcp_servers`` table
(``magi/bus/db/models/local/mcp_server.py``): the ``new_bus`` ORM
maps the same physical SQLite table with the same flat columns
as the old bus. ``__table_args__ = {"extend_existing": True}``
lets the two ORMs share the table without ``MetaData`` collisions
when each is registered in a different import order.

Both buses' tables target the same SQLite file. The new_bus
side owns the *write* path (via :class:`McpServerBook`); the old
bus still owns the *read* path used by the WebUI / LLM manage
tools while those modules migrate off it. See
``docs/MCP_WORKER_DESIGN.md`` for the migration roadmap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class McpServer:
    """One operator-configured MCP server.

    ``id`` is the autoincrement PK inherited from the new_bus
    schema (kept for future read-only callers that want the
    numeric handle). Operator-facing identity remains ``name``
    (matches the old bus PK); ``name`` is immutable — rename by
    delete + create.

    ``args`` / ``env`` / ``headers`` are deserialised from the
    JSON columns on the way out and serialised on the way in.
    The wire shape is identical to the old bus
    :class:`magi.bus.jobs.protocols.mcp.McpServerConfig`, minus
    the secret values masked at the API layer.
    """

    id: int
    name: str
    connection_type: str
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    connect_timeout: float | None = None
    execute_timeout: float | None = None
    sse_read_timeout: float | None = None
    created_at: str | None = None
    updated_at: str | None = None

    # Preserved on the row but not exposed on the DTO: the
    # ``config`` JSON blob is reserved for future read-only
    # callers that want a single denormalised payload. New
    # writes always go through the dedicated columns.
    config: dict[str, Any] = field(default_factory=dict)


# -- internal ORM --------------------------------------------------------


class _McpServerRow(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # STDIO
    command: Mapped[str | None] = mapped_column(String(256), nullable=True)
    args_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]",
    )
    env_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}",
    )

    # URL-based (sse / streamable_http)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    headers_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}",
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1",
    )

    # Per-server timeouts. ``None`` → worker uses
    # :class:`MCPTimeoutConfig` defaults read from settings_book.
    connect_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    execute_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    sse_read_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Reserved for future read-only callers — writes still go
    # through the dedicated columns. Kept as ``JSON`` to match
    # the old bus ``mcp_server.McpServer`` schema naming.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict,
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
        nullable=False,
    )


# -- helpers -------------------------------------------------------------


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
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.id == server_id)
            )
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> McpServer | None:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[McpServer]:
        with self._session() as s:
            rows = s.scalars(
                select(_McpServerRow).order_by(_McpServerRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self) -> list[McpServer]:
        """Return every row whose ``enabled`` column is true.

        Mirrors the old bus ``McpService.enabled_configs`` filter
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
        connection_type: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool | None = None,
        connect_timeout: float | None = None,
        execute_timeout: float | None = None,
        sse_read_timeout: float | None = None,
    ) -> None:
        """In-place update of a row by numeric id.

        The API surface takes ``server_id`` (not ``name``) because
        the new_bus schema carries the autoincrement PK alongside
        ``name``. The :meth:`upsert` wrapper below is the
        primary entry point — it looks the row up by name, then
        forwards to ``update`` for the actual mutation.
        """
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.id == server_id)
            )
            if row is None:
                return
            if connection_type is not None:
                row.connection_type = connection_type
            if command is not None:
                row.command = command
            if args is not None:
                row.args_json = json.dumps(args)
            if env is not None:
                row.env_json = json.dumps(env)
            if url is not None:
                row.url = url
            if headers is not None:
                row.headers_json = json.dumps(headers)
            if enabled is not None:
                row.enabled = enabled
            if connect_timeout is not None:
                row.connect_timeout = connect_timeout
            if execute_timeout is not None:
                row.execute_timeout = execute_timeout
            if sse_read_timeout is not None:
                row.sse_read_timeout = sse_read_timeout
            s.commit()

    def delete(self, *, server_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_McpServerRow).where(_McpServerRow.id == server_id)
            )
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

        Same shape as the old bus ``McpService.upsert`` (which
        is what the WebUI + LLM manage tools still call until
        they migrate to new_bus). Validates ``connection_type``
        + transport-specific required fields; raises
        :class:`ValueError` on bad input — the existing manage
        tools already catch this in their error envelopes.
        """
        if connection_type not in ("stdio", "sse", "streamable_http"):
            raise ValueError(
                "connection_type must be one of: stdio, sse, streamable_http"
            )
        if connection_type == "stdio" and not (command and command.strip()):
            raise ValueError("stdio servers require 'command'")
        if connection_type != "stdio" and not (url and url.strip()):
            raise ValueError(f"{connection_type} servers require 'url'")

        args_list = list(args) if args else []
        env_dict = env or {}
        headers_dict = headers or {}

        existing = self.get_by_name(name=name)
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
        self.update(
            server_id=existing.id,
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
        self.update(server_id=existing.id, enabled=new_enabled)
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
                # Naive UTC datetimes — match the to_iso contract
                # used by every other Book so the wire shape
                # carries an explicit ``Z`` suffix.
                if value.tzinfo is None:
                    kwargs[ts_name] = value.isoformat() + "Z"
                else:
                    kwargs[ts_name] = value.astimezone().isoformat().replace("+00:00", "Z")
            elif value is None:
                kwargs[ts_name] = None
            else:
                kwargs[ts_name] = value
        return McpServer(**kwargs)


__all__ = ["McpServer", "McpServerBook", "_McpServerRow"]
