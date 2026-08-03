"""Add idempotency-key columns + partial unique indexes.

Revision ID: 0009_idempotency_keys
Revises: 0008_merge_actor_and_auth_heads

Architecture §7 of docs/MAGI_BUS_CENTRIC_ARCHITECTURE.md requires
the bus tables to expose stable producer-supplied idempotency keys so that
the at-least-once delivery + idempotent-consumption contract holds across
process restarts and producer retries.

Idempotency semantics:

  - ``agent_inbox`` already has ``UNIQUE(event_id)`` (the local envelope
    key). This migration adds ``UNIQUE(source_type, source_id,
    external_event_id)`` as a *partial* index that only applies when
    ``external_event_id IS NOT NULL``. The partial form lets
    ``publish_agent_message`` dedupe across channels (a Telegram
    update_id redelivered after bot restart) without forcing every
    row to carry an external id.

  - ``tool_jobs`` and ``delivery_outbox`` gain an explicit
    ``idempotency_key`` column with a partial unique index. The
    actor transition uses ``f"tool:{run_id}:{tool_call_id}"`` and
    ``f"a2a:{invocation_id}"`` as defaults; producers may override.

  - ``delivery_outbox.event_id`` is a nullable correlation back to the
    triggering ``agent_inbox.event_id`` (e.g. one TG reply per
    inbound message). Partial unique index.

  - ``run_inputs.source_event_id`` is a *non-unique* index. The design
    spec calls for ``UNIQUE(source_event_id)`` on run_inputs but the
    existing ``UNIQUE(event_id)`` already covers the strict
    de-duplication surface (``event_id`` is the local envelope id;
    ``source_event_id`` is the upstream's id — they diverge on
    cross-channel redelivery). The index alone supports the audit
    query; making the column UNIQUE would over-constrain legitimate
    cross-channel steering. This is a documented deviation from
    design §6.5.

SQLite-only partial-index syntax
--------------------------------

The unique indexes below use ``sqlite_where=`` (SQLAlchemy's
SQLite-specific partial-index predicate). The private runtime
database is always SQLite; the public PostgreSQL schema is built
with ``Base.metadata.create_all`` and never runs Alembic, so these
partial constraints do not apply there. A future Alembic port for
PG must emit an equivalent ``WHERE`` clause (PostgreSQL has full
partial-index support; this is a transliteration, not a redesign).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_idempotency_keys"
down_revision = "0008_merge_actor_and_auth_heads"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    # agent_inbox: source discriminator + upstream-stable id
    _add("agent_inbox", sa.Column("source_type", sa.String(length=32), nullable=True))
    _add("agent_inbox", sa.Column("source_id", sa.String(length=128), nullable=True))
    _add("agent_inbox", sa.Column("external_event_id", sa.String(length=128), nullable=True))

    if "ux_agent_inbox_source_external" not in _indexes("agent_inbox"):
        op.create_index(
            "ux_agent_inbox_source_external",
            "agent_inbox",
            ["source_type", "source_id", "external_event_id"],
            unique=True,
            sqlite_where=sa.text("external_event_id IS NOT NULL"),
        )

    # tool_jobs: explicit producer idempotency
    _add("tool_jobs", sa.Column("idempotency_key", sa.String(length=160), nullable=True))
    if "ux_tool_jobs_idempotency" not in _indexes("tool_jobs"):
        op.create_index(
            "ux_tool_jobs_idempotency",
            "tool_jobs",
            ["idempotency_key"],
            unique=True,
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        )

    # delivery_outbox: optional correlation back to inbound + idempotency key
    _add("delivery_outbox", sa.Column("event_id", sa.String(length=64), nullable=True))
    _add("delivery_outbox", sa.Column("idempotency_key", sa.String(length=160), nullable=True))

    if "ux_delivery_outbox_event_id" not in _indexes("delivery_outbox"):
        op.create_index(
            "ux_delivery_outbox_event_id",
            "delivery_outbox",
            ["event_id"],
            unique=True,
            sqlite_where=sa.text("event_id IS NOT NULL"),
        )
    if "ux_delivery_outbox_idempotency" not in _indexes("delivery_outbox"):
        op.create_index(
            "ux_delivery_outbox_idempotency",
            "delivery_outbox",
            ["idempotency_key"],
            unique=True,
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        )

    # run_inputs: audit index only (deviation from design §6.5 documented above)
    _add("run_inputs", sa.Column("source_event_id", sa.String(length=64), nullable=True))
    if "ix_run_inputs_source_event_id" not in _indexes("run_inputs"):
        op.create_index(
            "ix_run_inputs_source_event_id",
            "run_inputs",
            ["source_event_id"],
        )


def downgrade() -> None:
    # Additive-only: indexes are dropped, columns left in place. A
    # destructive downgrade would also need to rebuild FK references;
    # we keep the additive columns so a stale ORM revision never
    # references a missing column.
    for name in (
        "ix_run_inputs_source_event_id",
        "ux_delivery_outbox_idempotency",
        "ux_delivery_outbox_event_id",
        "ux_tool_jobs_idempotency",
        "ux_agent_inbox_source_external",
    ):
        try:
            op.drop_index(name)
        except Exception:  # noqa: BLE001 — additive-only downgrade
            pass
