"""Durable Tool Catalog service.

The executable registry remains private to :mod:`magi.tools`. This service
owns the separate database-backed menu the agent is permitted to see.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from sqlalchemy import select

from magi.bus.contracts import ToolCatalogSnapshot, ToolDefinition
from magi.db.base import utcnow_naive
from magi.db.engine import open_session
from magi.db.models_tool import ToolCatalogState, ToolDefinitionRecord


class CatalogRevisionConflict(RuntimeError):
    """The caller attempted to replace a stale catalog snapshot."""


class ToolCatalogValidationError(ValueError):
    """A snapshot does not satisfy the durable catalog invariant."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _schema_hash(definition: ToolDefinition) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "name": definition.name,
                "source": definition.source,
                "description": definition.description,
                "input_schema": definition.input_schema,
                "allowed_roles": list(definition.allowed_roles),
                "implementation_version": definition.implementation_version,
            }
        ).encode()
    ).hexdigest()


def _as_contract(row: ToolDefinitionRecord) -> ToolDefinition:
    return ToolDefinition(
        name=row.name,
        source=row.source,
        description=row.description,
        input_schema=dict(row.input_schema),
        allowed_roles=tuple(row.allowed_roles or ()),
        enabled=bool(row.enabled),
        implementation_version=row.implementation_version,
        schema_hash=row.schema_hash,
        revision=row.revision,
    )


class ToolCatalogService:
    """SQLite repository/application service for the agent-visible catalog."""

    _STATE_KEY = "tool_catalog"

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir

    def get_snapshot(self) -> ToolCatalogSnapshot:
        with open_session(self._state_dir) as session:
            state = session.get(ToolCatalogState, self._STATE_KEY)
            rows = session.scalars(
                select(ToolDefinitionRecord).order_by(ToolDefinitionRecord.source, ToolDefinitionRecord.name)
            ).all()
            return ToolCatalogSnapshot(
                revision=state.current_revision if state is not None else 0,
                snapshot_hash=state.snapshot_hash if state is not None else "",
                definitions=tuple(_as_contract(row) for row in rows),
            )

    def replace_snapshot(
        self,
        *,
        source: str,
        definitions: Iterable[ToolDefinition],
        expected_previous_revision: int | None = None,
    ) -> ToolCatalogSnapshot:
        """Atomically replace one source's visible tools.

        Definitions missing from a replacement remain available for audit but
        are disabled. The global revision and snapshot hash advance exactly
        once per successful replacement.
        """
        source = source.strip()
        if not source:
            raise ToolCatalogValidationError("tool catalog source is required")
        normalized = tuple(definitions)
        seen: set[str] = set()
        for definition in normalized:
            if definition.source != source:
                raise ToolCatalogValidationError("definition source must match snapshot source")
            if not definition.name or definition.name in seen:
                raise ToolCatalogValidationError("tool names must be non-empty and unique per source")
            if not isinstance(definition.input_schema, dict):
                raise ToolCatalogValidationError(f"{definition.name}: input_schema must be an object")
            seen.add(definition.name)

        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            state = session.get(ToolCatalogState, self._STATE_KEY)
            if state is None:
                state = ToolCatalogState(
                    singleton_key=self._STATE_KEY, current_revision=0, snapshot_hash="", updated_at=now
                )
                session.add(state)
                session.flush()
            if expected_previous_revision is not None and state.current_revision != expected_previous_revision:
                raise CatalogRevisionConflict(
                    f"expected catalog revision {expected_previous_revision}, found {state.current_revision}"
                )
            # Provider calls use globally unique names, so reject an enabled
            # collision rather than allowing a worker to execute ambiguously.
            for name in seen:
                conflict = session.scalar(
                    select(ToolDefinitionRecord).where(
                        ToolDefinitionRecord.name == name,
                        ToolDefinitionRecord.source != source,
                        ToolDefinitionRecord.enabled.is_(True),
                    )
                )
                if conflict is not None:
                    raise ToolCatalogValidationError(
                        f"tool name {name!r} is already enabled by source {conflict.source!r}"
                    )

            revision = state.current_revision + 1
            old_rows = {
                row.name: row
                for row in session.scalars(
                    select(ToolDefinitionRecord).where(ToolDefinitionRecord.source == source)
                )
            }
            for definition in normalized:
                row = old_rows.pop(definition.name, None)
                digest = definition.schema_hash or _schema_hash(definition)
                if row is None:
                    session.add(
                        ToolDefinitionRecord(
                            source=source,
                            name=definition.name,
                            description=definition.description,
                            input_schema=dict(definition.input_schema),
                            allowed_roles=list(definition.allowed_roles),
                            enabled=definition.enabled,
                            implementation_version=definition.implementation_version,
                            schema_hash=digest,
                            revision=revision,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    row.description = definition.description
                    row.input_schema = dict(definition.input_schema)
                    row.allowed_roles = list(definition.allowed_roles)
                    row.enabled = definition.enabled
                    row.implementation_version = definition.implementation_version
                    row.schema_hash = digest
                    row.revision = revision
                    row.updated_at = now
            for row in old_rows.values():
                row.enabled = False
                row.revision = revision
                row.updated_at = now

            session.flush()
            all_rows = session.scalars(
                select(ToolDefinitionRecord).order_by(ToolDefinitionRecord.source, ToolDefinitionRecord.name)
            ).all()
            state.current_revision = revision
            state.snapshot_hash = hashlib.sha256(
                _canonical_json(
                    [
                        {
                            "source": row.source,
                            "name": row.name,
                            "schema_hash": row.schema_hash,
                            "enabled": row.enabled,
                            "revision": row.revision,
                        }
                        for row in all_rows
                    ]
                ).encode()
            ).hexdigest()
            state.updated_at = now
            session.commit()
            return ToolCatalogSnapshot(
                revision=revision,
                snapshot_hash=state.snapshot_hash,
                definitions=tuple(_as_contract(row) for row in all_rows),
            )

    def list_definitions(
        self, *, caller_role: str | None = None, enabled_only: bool = True
    ) -> list[ToolDefinition]:
        with open_session(self._state_dir) as session:
            stmt = select(ToolDefinitionRecord).order_by(ToolDefinitionRecord.source, ToolDefinitionRecord.name)
            if enabled_only:
                stmt = stmt.where(ToolDefinitionRecord.enabled.is_(True))
            rows = session.scalars(stmt).all()
        return [
            _as_contract(row)
            for row in rows
            if not row.allowed_roles or caller_role in set(row.allowed_roles)
        ]

    def list_schemas(self, *, caller_role: str | None = None, enabled_only: bool = True) -> list[dict]:
        return [
            {"name": item.name, "description": item.description, "input_schema": dict(item.input_schema)}
            for item in self.list_definitions(caller_role=caller_role, enabled_only=enabled_only)
        ]

    def get_definition(self, name: str, *, source: str | None = None) -> ToolDefinition | None:
        with open_session(self._state_dir) as session:
            stmt = select(ToolDefinitionRecord).where(ToolDefinitionRecord.name == name)
            if source is not None:
                stmt = stmt.where(ToolDefinitionRecord.source == source)
            row = session.scalar(stmt.order_by(ToolDefinitionRecord.id).limit(1))
            return _as_contract(row) if row is not None else None
