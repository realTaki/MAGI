"""ToolCatalogStateBook + ToolDefinitionBook — durable Tool Catalog.

Two tables:
- ``tool_catalog_state`` — singleton (id=1) holding the monotonic
  catalog revision + snapshot hash
- ``tool_definitions``   — one row per catalog tool

``ToolDefinition`` is the sole public DTO — it serves both read and
write paths. The Book owns serialization of ``input_schema`` and
``allowed_roles`` (stored as SQLAlchemy ``JSON`` columns).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from magi.old_bus.bases.db.base import enum_column


class ToolSource(StrEnum):
    """Origin discriminator stored on ``ToolDefinition.source``.

    ``BUILTIN`` covers the hard-coded toolset shipped with MAGI
    (filesystem, memory, contacts, MCP CRUD, …). ``MCP`` is
    everything discovered via a live MCP server connection.
    ``MANUAL`` is reserved for operator-registered tools not
    backed by an MCP server (e.g. a hand-rolled webhook bridge
    landing in a future PR); the row's ``default="manual"`` in
    the ORM keeps legacy single-source rows compatible.

    ``StrEnum`` rather than bare constants so typos are caught
    at lookup time instead of silently comparing False: every
    member is still a ``str`` (``ToolSource.BUILTIN == "builtin"``),
    so ``isinstance(x, str)`` checks, ``json.dumps`` serialisation,
    ``hash()`` consistency, and existing ``source == "builtin"``
    comparisons keep working unchanged. The ToolsWorker's
    ``upsert_many(source="builtin")`` keeps working because
    ``"builtin" in ToolSource`` (and the SAEnum column compare)
    are both True via str equality. Mirrors
    :class:`magi.bus.firmwares.books.local.contactBook.NoteKind`.
    """

    BUILTIN = "builtin"
    MCP = "mcp"
    MANUAL = "manual"


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCatalogState(BaseRecord):
    """Singleton catalog-state row DTO."""

    revision: int  # catalog 单调递增版本号
    snapshot_hash: str  # 当前快照的指纹哈希


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolDefinition(BaseRecord):
    """LLM-contract DTO — the tool as the agent sees it.

    This is the **only** public DTO for tool definitions.  It is used
    for both reads (returned by :meth:`ToolDefinitionBook.list_enabled`)
    and writes (passed to :meth:`ToolDefinitionBook.upsert_many`).
    The Book owns serialization of ``input_schema`` and ``allowed_roles``
    (stored as SQLAlchemy ``JSON`` columns).
    """

    name: str  # 工具唯一名
    source: ToolSource  # 工具来源（builtin/mcp/manual）
    description: str  # 工具描述（暴露给 LLM）
    input_schema: dict[str, Any]  # 入参 JSON schema
    allowed_roles: list[str] = field(default_factory=list)  # 允许调用此工具的角色
    enabled: bool = True  # 是否启用
    implementation_version: str | None = None  # 实现版本
    schema_hash: str = ""  # 输入 schema 指纹
    revision: int = 0  # 所属 catalog 版本号


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolCatalogSnapshot:
    """Observable state after an atomic catalog replacement."""

    revision: int  # 快照版本号
    snapshot_hash: str  # 快照内容指纹
    definitions: tuple[ToolDefinition, ...]  # 包含的全部工具定义


# -- internal ORM --------------------------------------------------------


class _ToolCatalogStateRow(BaseRecordMixin):
    __tablename__ = "tool_catalog_state"

    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")  # sha256 hex


class _ToolDefinitionRow(BaseRecordMixin):
    __tablename__ = "tool_definitions"

    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[ToolSource] = mapped_column(
        enum_column(ToolSource), nullable=False, default=ToolSource.MANUAL
    )
    # JSON list[str]; empty list = no role gate.
    allowed_roles: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )

    __table_args__ = (UniqueConstraint("name", name="uq_tool_definitions_name"),)


# -- Books ---------------------------------------------------------------


class ToolCatalogStateBook(BaseBook[_ToolCatalogStateRow, ToolCatalogState]):
    model_cls = _ToolCatalogStateRow
    record_cls = ToolCatalogState

    def get_current(self) -> ToolCatalogState | None:
        with self._session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            return self.record_cls.from_row(row) if row else None

    def replace_snapshot(
        self,
        *,
        revision: int,
        snapshot_hash: str,
    ) -> ToolCatalogState:
        """Atomic replacement of the singleton catalog state.

        The tools worker is the single writer today; when concurrent
        writers appear the optimistic-lock check goes in the caller.
        """
        with self._session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            if row is None:
                row = _ToolCatalogStateRow(
                    revision=revision,
                    snapshot_hash=snapshot_hash,
                )
                s.add(row)
            else:
                row.revision = revision
                row.snapshot_hash = snapshot_hash
            s.commit()
            s.refresh(row)
        return self.record_cls.from_row(row)


class ToolDefinitionBook(BaseBook[_ToolDefinitionRow, ToolDefinition]):
    model_cls = _ToolDefinitionRow
    record_cls = ToolDefinition

    def _apply_definition(
        self,
        dto: ToolDefinition,
        row: _ToolDefinitionRow,
        *,
        update_source: bool,
    ) -> None:
        """Serialize semantic fields into storage columns on an ORM row.

        ``update_source``: when creating a new row, set ``source`` from
        the DTO.  When updating an existing row that was matched via a
        source filter, preserve the existing value (the filter already
        guarantees it matches).
        """
        row.input_schema = dto.input_schema
        row.description = dto.description
        row.enabled = dto.enabled
        row.revision = dto.revision
        row.allowed_roles = dto.allowed_roles
        if update_source:
            row.source = dto.source

    # -- reads -----------------------------------------------------------

    def list_enabled(
        self,
        *,
        caller_role: str | None = None,
        caller_admin: bool = False,
    ) -> list[ToolDefinition]:
        """All enabled rows as :class:`ToolDefinition` DTOs.

        When ``caller_role``/``caller_admin`` are given, only rows whose
        ``allowed_roles`` permit that caller are returned (see
        :func:`_role_allowed`).  Called with no args, returns every enabled
        tool (backwards-compatible with the catalog-publish path).
        """
        with self._session() as s:
            rows = s.scalars(
                select(_ToolDefinitionRow)
                .where(_ToolDefinitionRow.enabled.is_(True))
                .order_by(_ToolDefinitionRow.name)
            ).all()
            dtos = [self.record_cls.from_row(r) for r in rows]
        if caller_role is None and not caller_admin:
            return dtos
        return [d for d in dtos if _role_allowed(d.allowed_roles, caller_role, caller_admin)]

    def get_by_name(self, *, name: str) -> ToolDefinition | None:
        """One definition by tool name, or ``None`` when unknown.

        ``schema_hash`` on the returned DTO is always ``""`` — the
        column doesn't persist it. Callers that need the fingerprint
        recompute it from the semantic fields, which round-trip
        exactly through :meth:`_apply_definition` / :meth:`ToolDefinition.from_row`
        (see :func:`magi.tools.worker._schema_hash`).
        """
        with self._session() as s:
            row = s.scalar(select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name))
            return self.record_cls.from_row(row) if row else None

    def list_schemas(
        self,
        *,
        caller_role: str | None = None,
        caller_admin: bool = False,
    ) -> list[dict[str, Any]]:
        """Anthropic-shaped schemas for the caller, role-filtered.

        Mirrors :func:`magi.tools.registry.get_tool_schemas` so the
        agent loop can swap implementations without changing call sites.
        """
        out: list[dict[str, Any]] = []
        for d in self.list_enabled():
            if not _role_allowed(d.allowed_roles, caller_role, caller_admin):
                continue
            out.append(
                {
                    "name": d.name,
                    "description": d.description,
                    "input_schema": d.input_schema,
                }
            )
        return out

    # -- writes ----------------------------------------------------------

    def upsert_many(
        self,
        *,
        definitions: list[ToolDefinition],
        source: str = "builtin",
    ) -> None:
        """Bulk upsert definitions in a single transaction.

        Only rows matching ``source`` are updated — rows with other
        sources (future MCP) are left alone.  New rows are created with
        ``dto.source``.

        Used by the tools worker's catalog publish path so all builtin
        definitions land atomically.
        """
        with self._session() as s:
            names = [d.name for d in definitions]
            existing: dict[str, _ToolDefinitionRow] = {}
            if names:
                rows = s.scalars(
                    select(_ToolDefinitionRow).where(
                        _ToolDefinitionRow.name.in_(names),
                        _ToolDefinitionRow.source == source,
                    )
                ).all()
                existing = {r.name: r for r in rows}
            for d in definitions:
                target = existing.get(d.name)
                if target is None:
                    target = _ToolDefinitionRow(name=d.name)
                    self._apply_definition(d, target, update_source=True)
                    s.add(target)
                else:
                    self._apply_definition(d, target, update_source=False)
            s.commit()


# -- internal helpers ----------------------------------------------------


def _role_allowed(
    allowed_roles: list[str],
    caller_role: str | None,
    caller_admin: bool,
) -> bool:
    """Whether this catalog row is visible to the caller."""
    if caller_admin:
        return True
    if not allowed_roles:
        return True
    if caller_role is None:
        return True
    return caller_role in allowed_roles


__all__ = [
    "ToolCatalogState",
    "ToolDefinition",
    "ToolCatalogSnapshot",
    "ToolCatalogStateBook",
    "ToolDefinitionBook",
    "ToolSource",
    "_ToolCatalogStateRow",
    "_ToolDefinitionRow",
]
