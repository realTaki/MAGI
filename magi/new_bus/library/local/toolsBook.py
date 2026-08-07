"""ToolCatalogStateBook + ToolDefinitionBook — durable Tool Catalog.

Two tables:
- ``tool_catalog_state`` — singleton (id=1) holding the monotonic
  catalog revision + snapshot hash
- ``tool_definitions``   — one row per catalog tool

Schema mirrors the old bus's ``tool_catalog_state`` + ``tool_definitions``.

This file also owns the LLM-contract DTOs (``ToolDefinition`` and
``ToolCatalogSnapshot``) — they describe what's *in* the catalog,
so they live next to the Books that publish them. Execution-
facing DTOs (:class:`ToolContext`, :class:`ToolResult`) live in
:mod:`magi.tools.base` next to the :class:`Tool` class.
"""

from __future__ import annotations

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

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCatalogState:
    id: int
    revision: int
    snapshot_hash: str


#: Persistent-row DTO. The LLM-contract DTO with the same name
#: (``ToolDefinition``) lives in this file too — keep them
#: apart by import path: the contract DTO has the LLM-visible
#: fields (``input_schema``, ``allowed_roles``, ``schema_hash``)
#: while this one has the storage shape (``spec_json``).
@dataclass(frozen=True, slots=True)
class ToolDefinitionRow:
    id: int
    name: str
    spec_json: str
    spec_dict: dict[str, Any] | None = None
    revision: int = 0
    enabled: int = 1
    description: str | None = None
    source: str = "manual"


#: LLM-contract DTO — what the agent sees as a menu item. Pure
#: data, crosses worker/agent/HTTP without exposing a registry
#: or ORM row. ``schema_hash`` lets the worker detect that an
#: agent's enqueued call used a stale menu (the catalog moved
#: forward between the agent's LLM call and the tool claim).
@dataclass(frozen=True, slots=True)
class ToolDefinition:
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
    spec_dict: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    __table_args__ = (UniqueConstraint("name", name="uq_tool_definitions_name"),)


# -- Books ---------------------------------------------------------------


class ToolCatalogStateBook(BaseBook[_ToolCatalogStateRow, ToolCatalogState]):
    model_cls = _ToolCatalogStateRow
    dto_cls = ToolCatalogState

    def get(self) -> ToolCatalogState | None:
        with self._factory.session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            return self._row_to_dto(row) if row else None

    def bump(self, *, revision: int, snapshot_hash: str) -> ToolCatalogState:
        with self._factory.session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            if row is None:
                row = _ToolCatalogStateRow(
                    revision=revision, snapshot_hash=snapshot_hash
                )
                s.add(row)
            else:
                row.revision = revision
                row.snapshot_hash = snapshot_hash
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def replace_snapshot(
        self,
        *,
        revision: int,
        snapshot_hash: str,
    ) -> ToolCatalogState:
        """Atomic replacement of the singleton catalog state.

        The tools worker is the single writer today (MCP gets
        its own worker later), so the optimistic-lock check
        lives in the worker if/when concurrent writers appear.
        This method just writes the new revision + hash
        atomically.

        ``revision`` should be monotonically increasing from
        the caller's POV; the book doesn't enforce it (yet) —
        when a second writer arrives, port the
        ``expected_previous_revision`` check from the old
        ``ToolCatalogService.replace_snapshot``.
        """
        with self._factory.session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            if row is None:
                row = _ToolCatalogStateRow(
                    revision=revision, snapshot_hash=snapshot_hash,
                )
                s.add(row)
            else:
                row.revision = revision
                row.snapshot_hash = snapshot_hash
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


class ToolDefinitionBook(BaseBook[_ToolDefinitionRow, ToolDefinitionRow]):
    model_cls = _ToolDefinitionRow
    dto_cls = ToolDefinitionRow

    def get(self, *, tool_id: int) -> ToolDefinitionRow | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.id == tool_id)
            )
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> ToolDefinitionRow | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[ToolDefinitionRow]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ToolDefinitionRow).order_by(_ToolDefinitionRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_enabled(self) -> list[ToolDefinitionRow]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_ToolDefinitionRow)
                .where(_ToolDefinitionRow.enabled == 1)
                .order_by(_ToolDefinitionRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def upsert(self, *, name: str, spec_json: str, revision: int = 0,
               description: str | None = None, source: str = "manual",
               spec_dict: str | None = None) -> ToolDefinitionRow:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            if row is None:
                row = _ToolDefinitionRow(
                    name=name, spec_json=spec_json, revision=revision,
                    description=description, source=source, spec_dict=spec_dict,
                )
                s.add(row)
            else:
                row.spec_json = spec_json
                row.revision = revision
                row.description = description
                row.source = source
                row.spec_dict = spec_dict
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def set_enabled(self, *, name: str, enabled: bool) -> None:
        """Toggle ``enabled`` for one tool by name.

        Does not delete the row — disabled tools are retained so
        history (e.g. audit logs, schema snapshots) stays
        queryable. Returns silently if the name is unknown; the
        caller decides whether to treat that as an error.
        """
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            if row is None:
                return
            row.enabled = 1 if enabled else 0
            s.commit()

    def delete(self, *, name: str) -> None:
        """Permanently remove a tool row.

        Distinct from :meth:`set_enabled` (which retains the
        row with ``enabled=0``). Use for MCP catalog teardown
        when a server is decommissioned — not for routine
        disable.
        """
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            if row is None:
                return
            s.delete(row)
            s.commit()

    def upsert_many(self, *, definitions: list[ToolDefinitionRow]) -> None:
        """Bulk upsert definitions in a single transaction.

        Used by the tools worker's catalog publish path — all
        builtin definitions land in one atomic write so the
        agent never sees a half-published catalog. Existing
        rows are updated in place; the supplied list is
        authoritative for ``source='builtin'`` rows (existing
        rows with the same name but a different source are left
        alone — that's MCP's concern).
        """
        with self._factory.session() as s:
            names = [d.name for d in definitions]
            existing = {}
            if names:
                rows = s.scalars(
                    select(_ToolDefinitionRow).where(
                        _ToolDefinitionRow.name.in_(names),
                        _ToolDefinitionRow.source == "builtin",
                    )
                ).all()
                existing = {r.name: r for r in rows}
            for d in definitions:
                row = existing.get(d.name)
                if row is None:
                    s.add(_ToolDefinitionRow(
                        name=d.name, spec_json=d.spec_json,
                        spec_dict=d.spec_dict, revision=d.revision,
                        enabled=d.enabled, description=d.description,
                        source=d.source,
                    ))
                else:
                    row.spec_json = d.spec_json
                    row.spec_dict = d.spec_dict
                    row.revision = d.revision
                    row.enabled = d.enabled
                    row.description = d.description
                    # source is the foreign key for "who owns
                    # this row"; don't change it on upsert.
            s.commit()


__all__ = [
    "ToolCatalogState",
    "ToolDefinitionRow",
    "ToolDefinition",
    "ToolCatalogSnapshot",
    "ToolCatalogStateBook",
    "ToolDefinitionBook",
    "_ToolCatalogStateRow",
    "_ToolDefinitionRow",
]