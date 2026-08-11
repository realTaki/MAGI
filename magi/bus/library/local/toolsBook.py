"""ToolCatalogStateBook + ToolDefinitionBook — durable Tool Catalog.

Two tables:
- ``tool_catalog_state`` — singleton (id=1) holding the monotonic
  catalog revision + snapshot hash
- ``tool_definitions``   — one row per catalog tool

``ToolDefinition`` is the sole public DTO — it serves both read and
write paths. The Book handles serialization of semantic fields
(``input_schema`` → ``spec_json``, ``allowed_roles`` → ``allowed_roles_json``)
internally.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    BigInteger,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base
from magi.bus.library.base import BaseBook

# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCatalogState:
    """Singleton catalog-state row DTO."""

    id: int
    revision: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """LLM-contract DTO — the tool as the agent sees it.

    This is the **only** public DTO for tool definitions.  It is used
    for both reads (returned by :meth:`ToolDefinitionBook.list_enabled`)
    and writes (passed to :meth:`ToolDefinitionBook.upsert_many`).
    The Book owns serialization of ``input_schema`` (→ ``spec_json``)
    and ``allowed_roles`` (→ ``allowed_roles_json``).
    """

    name: str
    source: str
    description: str
    input_schema: dict[str, Any]
    allowed_roles: tuple[str, ...] = ()
    enabled: bool = True
    implementation_version: str | None = None
    schema_hash: str = ""
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ToolCatalogSnapshot:
    """Observable state after an atomic catalog replacement."""

    revision: int
    snapshot_hash: str
    definitions: tuple[ToolDefinition, ...]


# -- internal ORM --------------------------------------------------------


class _ToolCatalogStateRow(Base):
    __tablename__ = "tool_catalog_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class _ToolDefinitionRow(Base):
    __tablename__ = "tool_definitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    #: Deprecated duplicate of ``spec_json``; kept for schema compatibility
    #: but no longer written by ToolDefinitionBook.  New rows get NULL.
    spec_dict: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # JSON-serialized list[str] or NULL (no role gate).
    allowed_roles_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("name", name="uq_tool_definitions_name"),)


# -- Books ---------------------------------------------------------------


class ToolCatalogStateBook(BaseBook[_ToolCatalogStateRow, ToolCatalogState]):
    model_cls = _ToolCatalogStateRow
    dto_cls = ToolCatalogState

    def get(self) -> ToolCatalogState | None:
        with self._session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            return self._row_to_dto(row) if row else None

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
        return self._row_to_dto(row)


class ToolDefinitionBook(BaseBook[_ToolDefinitionRow, ToolDefinition]):
    model_cls = _ToolDefinitionRow
    dto_cls = ToolDefinition

    # -- mapping ---------------------------------------------------------

    def _row_to_dto(self, row: _ToolDefinitionRow) -> ToolDefinition:
        """Deserialize storage columns → semantic :class:`ToolDefinition`."""
        try:
            input_schema = json.loads(row.spec_json) if row.spec_json else {}
        except json.JSONDecodeError:
            input_schema = {}
        return ToolDefinition(
            name=row.name,
            source=row.source,
            description=row.description or "",
            input_schema=input_schema,
            allowed_roles=_parse_allowed_roles(row.allowed_roles_json),
            enabled=bool(row.enabled),
            revision=row.revision,
        )

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
        row.spec_json = json.dumps(dto.input_schema, ensure_ascii=False)
        row.description = dto.description or None
        row.enabled = 1 if dto.enabled else 0
        row.revision = dto.revision
        row.allowed_roles_json = (
            json.dumps(list(dto.allowed_roles), ensure_ascii=False) if dto.allowed_roles else None
        )
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
                .where(_ToolDefinitionRow.enabled == 1)
                .order_by(_ToolDefinitionRow.name)
            ).all()
            dtos = [self._row_to_dto(r) for r in rows]
        if caller_role is None and not caller_admin:
            return dtos
        return [d for d in dtos if _role_allowed(d.allowed_roles, caller_role, caller_admin)]

    def get_by_name(self, *, name: str) -> ToolDefinition | None:
        """One definition by tool name, or ``None`` when unknown.

        ``schema_hash`` on the returned DTO is always ``""`` — the
        column doesn't persist it. Callers that need the fingerprint
        recompute it from the semantic fields, which round-trip
        exactly through :meth:`_apply_definition` / :meth:`_row_to_dto`
        (see :func:`magi.tools.worker._schema_hash`).
        """
        with self._session() as s:
            row = s.scalar(select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name))
            return self._row_to_dto(row) if row else None

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


def _parse_allowed_roles(json_str: str | None) -> tuple[str, ...]:
    """Decode ``allowed_roles_json`` → tuple, tolerating bad data."""
    if not json_str:
        return ()
    try:
        raw = json.loads(json_str)
    except json.JSONDecodeError:
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(str(r) for r in raw if isinstance(r, str))


def _role_allowed(
    allowed_roles: tuple[str, ...],
    caller_role: str | None,
    caller_admin: bool,
) -> bool:
    """Mirror of :meth:`Tool.gate` (role + admin check)."""
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
    "_ToolCatalogStateRow",
    "_ToolDefinitionRow",
]
