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
    #: JSON-serialized tuple[str, ...] (or None when no role gate).
    #: Stored as a JSON string to match the existing ``spec_json`` /
    #: ``spec_dict`` convention — callers that need the typed tuple
    #: deserialize via :func:`_parse_allowed_roles` (or use
    #: :meth:`ToolDefinitionBook.list_definitions` which already does it).
    allowed_roles_json: str | None = None


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
    # JSON-serialized list[str] (or NULL when no role gate). Stored as
    # Text to match ``spec_json``/``spec_dict`` convention.
    allowed_roles_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("name", name="uq_tool_definitions_name"),)


# -- Books ---------------------------------------------------------------


class ToolCatalogStateBook(BaseBook[_ToolCatalogStateRow, ToolCatalogState]):
    model_cls = _ToolCatalogStateRow
    dto_cls = ToolCatalogState

    def get(self) -> ToolCatalogState | None:
        with self._factory.session() as s:
            row = s.scalar(select(_ToolCatalogStateRow).limit(1))
            return self._row_to_dto(row) if row else None

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

    def list_definitions(self) -> list[ToolDefinition]:
        """All enabled rows as :class:`ToolDefinition` LLM-contract DTOs.

        This is the read path the agent loop / API will migrate to —
        returns the typed contract DTO with ``allowed_roles`` as a
        proper tuple and ``input_schema`` already deserialized. Today
        both call sites still go through the legacy ``bus.tool_catalog``
        service; this method exists so the migration can switch over
        in one place.
        """
        out: list[ToolDefinition] = []
        for r in self.list_enabled():
            try:
                input_schema = json.loads(r.spec_json) if r.spec_json else {}
            except json.JSONDecodeError:
                input_schema = {}
            allowed = _parse_allowed_roles(r.allowed_roles_json)
            out.append(ToolDefinition(
                name=r.name,
                source=r.source,
                description=r.description or "",
                input_schema=input_schema,
                allowed_roles=allowed,
                enabled=bool(r.enabled),
                implementation_version=None,
                revision=r.revision,
            ))
        return out

    def list_schemas(
        self,
        *,
        caller_role: str | None = None,
        caller_admin: bool = False,
    ) -> list[dict[str, Any]]:
        """Anthropic-shaped schemas for the caller, role-filtered.

        Mirrors :func:`magi.tools.registry.get_tool_schemas` so the
        agent loop can swap implementations without changing call
        sites. ``caller_admin=True`` bypasses the role enum (WebUI
        operator shortcut); ``caller_role=None`` falls through the
        permissive branch (``ALLOWED_ROLES`` empty OR unknown caller
        — see :func:`_role_allowed`).
        """
        out: list[dict[str, Any]] = []
        for d in self.list_definitions():
            if not _role_allowed(d.allowed_roles, caller_role, caller_admin):
                continue
            out.append({
                "name": d.name,
                "description": d.description,
                "input_schema": d.input_schema,
            })
        return out

    def upsert(self, *, name: str, spec_json: str, revision: int = 0,
               description: str | None = None, source: str = "manual",
               spec_dict: str | None = None,
               allowed_roles_json: str | None = None) -> ToolDefinitionRow:
        with self._factory.session() as s:
            row = s.scalar(
                select(_ToolDefinitionRow).where(_ToolDefinitionRow.name == name)
            )
            if row is None:
                row = _ToolDefinitionRow(
                    name=name, spec_json=spec_json, revision=revision,
                    description=description, source=source, spec_dict=spec_dict,
                    allowed_roles_json=allowed_roles_json,
                )
                s.add(row)
            else:
                row.spec_json = spec_json
                row.revision = revision
                row.description = description
                row.source = source
                row.spec_dict = spec_dict
                row.allowed_roles_json = allowed_roles_json
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
                        allowed_roles_json=d.allowed_roles_json,
                    ))
                else:
                    row.spec_json = d.spec_json
                    row.spec_dict = d.spec_dict
                    row.revision = d.revision
                    row.enabled = d.enabled
                    row.description = d.description
                    row.allowed_roles_json = d.allowed_roles_json
                    # source is the foreign key for "who owns
                    # this row"; don't change it on upsert.
            s.commit()


# -- internal helpers ------------------------------------------------------


def _parse_allowed_roles(json_str: str | None) -> tuple[str, ...]:
    """Decode the ``allowed_roles_json`` column back into a tuple.

    Tolerates bad data (corrupt row, empty list, non-string entries)
    by returning an empty tuple — the caller's role filter then
    treats the tool as unrestricted, which matches what an
    admin-only ``ALLOWED_ROLES`` would do if it were silently
    dropped during publish.
    """
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
    """Mirror of :meth:`Tool.is_allowed_for_role` (kept inline to
    avoid a new_bus → tools layer dependency).

    Behavior must stay aligned with :class:`magi.tools.base.Tool`'s
    implementation — both are tested by the same fixtures. See
    that method's docstring for the rationale of each branch.
    """
    if caller_admin:
        return True
    if not allowed_roles:
        return True
    if caller_role is None:
        return True
    return caller_role in allowed_roles


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